/**
 * Admit WhatsApp groups by who created them.
 *
 * `group_policy: allowlist` admits a group only when its JID is listed. With
 * `group_allow_creators` set, a group that is not listed is also admitted when
 * WhatsApp reports that one of those people created it. This lets an owner
 * start a new group and have the agent answer there without editing config.
 *
 * The gate also reports whether every member of the group is an allowed user.
 * The adapter uses that to answer freely in a private group and to wait until
 * it is addressed once other people have been added.
 *
 * Group details are read from `sock.groupMetadata()` and cached, because every
 * inbound group message passes through this check. Membership changes, so the
 * cache is short and is dropped when WhatsApp reports a participant change. A
 * failed lookup is cached for a minute only, so a group that could not be read
 * (network blip, bot not yet a member) is retried rather than blocked forever.
 *
 * Kept separate from bridge.js so the decision can be tested without Baileys.
 */

import { matchesAllowedUser, normalizeWhatsAppIdentifier } from './allowlist.js';

const KNOWN_GROUP_TTL_MS = 60 * 60 * 1000;
const FAILED_LOOKUP_TTL_MS = 60 * 1000;
const MAX_CACHED_GROUPS = 500;

/**
 * Every identifier WhatsApp gave for the creator. A group can report the
 * creator as a LID, as a phone JID, or both, depending on its addressing mode.
 */
export function creatorIdsFromMetadata(metadata) {
  if (!metadata) return [];
  const ids = [metadata.owner, metadata.ownerPn]
    .map((value) => normalizeWhatsAppIdentifier(value))
    .filter(Boolean);
  return [...new Set(ids)];
}

export function creatorIsAllowed(creatorIds, allowedCreators, sessionDir) {
  if (!allowedCreators || allowedCreators.size === 0) return false;
  // "*" would admit every group anyone creates; that is `group_policy: open`, not this setting.
  if (allowedCreators.has('*')) return false;
  return creatorIds.some((id) => matchesAllowedUser(id, allowedCreators, sessionDir));
}

/** True when the group has members and each one is an allowed user or the bot itself. */
export function membersAreAllAllowed(participants, allowedMembers, botIds, sessionDir) {
  if (!Array.isArray(participants) || participants.length === 0) return false;
  const bots = new Set((botIds || []).map((id) => normalizeWhatsAppIdentifier(id)).filter(Boolean));
  return participants.every((participant) => {
    const ids = [participant?.id, participant?.lid, participant?.phoneNumber]
      .map((value) => normalizeWhatsAppIdentifier(value))
      .filter(Boolean);
    return ids.some((id) => bots.has(id) || matchesAllowedUser(id, allowedMembers, sessionDir));
  });
}

export function createGroupCreatorGate({
  fetchGroupMetadata,
  allowedCreators,
  allowedMembers,
  sessionDir,
  now = () => Date.now(),
}) {
  const cache = new Map(); // group JID -> { creatorIds, subject, participants, expiresAt }

  function remember(chatId, entry) {
    if (cache.size >= MAX_CACHED_GROUPS && !cache.has(chatId)) {
      cache.delete(cache.keys().next().value);
    }
    cache.set(chatId, entry);
    return entry;
  }

  async function lookup(chatId) {
    const cached = cache.get(chatId);
    if (cached && cached.expiresAt > now()) return cached;
    try {
      const metadata = await fetchGroupMetadata(chatId);
      const creatorIds = creatorIdsFromMetadata(metadata);
      return remember(chatId, {
        creatorIds,
        subject: metadata?.subject || '',
        participants: metadata?.participants || [],
        expiresAt: now() + (creatorIds.length ? KNOWN_GROUP_TTL_MS : FAILED_LOOKUP_TTL_MS),
      });
    } catch (error) {
      return remember(chatId, {
        creatorIds: [],
        subject: '',
        participants: [],
        error: String(error?.message || error),
        expiresAt: now() + FAILED_LOOKUP_TTL_MS,
      });
    }
  }

  return {
    enabled: Boolean(allowedCreators && allowedCreators.size > 0),

    /** Resolves to { admitted, creatorIds, subject, membersAllAllowed, error? } for one group. */
    async check(chatId, botIds = []) {
      if (!this.enabled) return { admitted: false, creatorIds: [], subject: '', membersAllAllowed: false };
      const entry = await lookup(chatId);
      const admitted = creatorIsAllowed(entry.creatorIds, allowedCreators, sessionDir);
      return {
        admitted,
        creatorIds: entry.creatorIds,
        subject: entry.subject,
        membersAllAllowed: admitted && membersAreAllAllowed(entry.participants, allowedMembers, botIds, sessionDir),
        ...(entry.error ? { error: entry.error } : {}),
      };
    },

    /** Forget one group so its next message re-reads the member list. */
    forget(chatId) {
      cache.delete(chatId);
    },
  };
}
