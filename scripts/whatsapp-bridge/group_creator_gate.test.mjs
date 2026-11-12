import test from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';

import { parseAllowedUsers } from './allowlist.js';
import {
  createGroupCreatorGate,
  creatorIdsFromMetadata,
  creatorIsAllowed,
  membersAreAllAllowed,
} from './group_creator_gate.js';

const GROUP = '120363001234567890@g.us';
const OWNER_PHONE = '15550100001';
const OWNER_LID = '200000000000001';
const STRANGER_LID = '200000000000099';

function gateFor({ metadataByGroup = {}, allowed = OWNER_PHONE, members, sessionDir = '/nonexistent', clock = { t: 0 } }) {
  const calls = [];
  const gate = createGroupCreatorGate({
    fetchGroupMetadata: async (chatId) => {
      calls.push(chatId);
      const value = metadataByGroup[chatId];
      if (value instanceof Error) throw value;
      return value;
    },
    allowedCreators: parseAllowedUsers(allowed),
    allowedMembers: parseAllowedUsers(members ?? allowed),
    sessionDir,
    now: () => clock.t,
  });
  return { gate, calls, clock };
}

test('creatorIdsFromMetadata reads both the LID and the phone form', () => {
  assert.deepEqual(
    creatorIdsFromMetadata({ owner: `${OWNER_LID}@lid`, ownerPn: `${OWNER_PHONE}@s.whatsapp.net` }),
    [OWNER_LID, OWNER_PHONE],
  );
  assert.deepEqual(creatorIdsFromMetadata({ owner: undefined }), []);
  assert.deepEqual(creatorIdsFromMetadata(null), []);
});

test('a group created by an allowed phone number is admitted', async () => {
  const { gate } = gateFor({ metadataByGroup: { [GROUP]: { subject: 'Trip', owner: `${OWNER_PHONE}@s.whatsapp.net` } } });
  assert.deepEqual(await gate.check(GROUP), {
    admitted: true, creatorIds: [OWNER_PHONE], subject: 'Trip', membersAllAllowed: false,
  });
});

test('a group reporting only the creator LID is admitted when the phone twin is present', async () => {
  const { gate } = gateFor({
    metadataByGroup: { [GROUP]: { subject: 'Trip', owner: `${OWNER_LID}@lid`, ownerPn: `${OWNER_PHONE}@s.whatsapp.net` } },
  });
  assert.equal((await gate.check(GROUP)).admitted, true);
});

test('a creator LID is matched to an allowed phone number through the session mapping files', async () => {
  const sessionDir = mkdtempSync(path.join(os.tmpdir(), 'hermes-wa-creator-'));
  try {
    writeFileSync(path.join(sessionDir, `lid-mapping-${OWNER_LID}_reverse.json`), JSON.stringify(OWNER_PHONE));
    const { gate } = gateFor({ metadataByGroup: { [GROUP]: { subject: 'Trip', owner: `${OWNER_LID}@lid` } }, sessionDir });
    assert.equal((await gate.check(GROUP)).admitted, true);
  } finally {
    rmSync(sessionDir, { recursive: true, force: true });
  }
});

test('a group created by someone else is rejected', async () => {
  const { gate } = gateFor({ metadataByGroup: { [GROUP]: { subject: 'Spam', owner: `${STRANGER_LID}@lid` } } });
  const verdict = await gate.check(GROUP);
  assert.equal(verdict.admitted, false);
  assert.deepEqual(verdict.creatorIds, [STRANGER_LID]);
});

test('a group whose creator WhatsApp does not report is rejected', async () => {
  const { gate } = gateFor({ metadataByGroup: { [GROUP]: { subject: 'Old group', owner: undefined } } });
  assert.equal((await gate.check(GROUP)).admitted, false);
});

test('a failed metadata lookup rejects the group and reports the error', async () => {
  const { gate } = gateFor({ metadataByGroup: { [GROUP]: new Error('forbidden') } });
  assert.deepEqual(await gate.check(GROUP), {
    admitted: false, creatorIds: [], subject: '', membersAllAllowed: false, error: 'forbidden',
  });
});

test('an empty creator list disables the gate without calling WhatsApp', async () => {
  const { gate, calls } = gateFor({ metadataByGroup: { [GROUP]: { owner: `${OWNER_PHONE}@s.whatsapp.net` } }, allowed: '' });
  assert.equal(gate.enabled, false);
  assert.equal((await gate.check(GROUP)).admitted, false);
  assert.deepEqual(calls, []);
});

test('a wildcard creator never admits a group', () => {
  assert.equal(creatorIsAllowed([STRANGER_LID], parseAllowedUsers('*'), '/nonexistent'), false);
});

test('the creator is looked up once per group and then served from cache', async () => {
  const { gate, calls } = gateFor({ metadataByGroup: { [GROUP]: { subject: 'Trip', owner: `${OWNER_PHONE}@s.whatsapp.net` } } });
  await gate.check(GROUP);
  await gate.check(GROUP);
  await gate.check(GROUP);
  assert.deepEqual(calls, [GROUP]);
});

test('a failed lookup is retried after a minute, a known group is kept for an hour', async () => {
  const metadataByGroup = { [GROUP]: new Error('timeout') };
  const { gate, calls, clock } = gateFor({ metadataByGroup });

  assert.equal((await gate.check(GROUP)).admitted, false);
  clock.t = 30 * 1000;
  await gate.check(GROUP);
  assert.equal(calls.length, 1, 'no retry inside the failure window');

  metadataByGroup[GROUP] = { subject: 'Trip', owner: `${OWNER_PHONE}@s.whatsapp.net` };
  clock.t = 61 * 1000;
  assert.equal((await gate.check(GROUP)).admitted, true, 'retried once the failure window passed');
  assert.equal(calls.length, 2);

  clock.t += 59 * 60 * 1000;
  await gate.check(GROUP);
  assert.equal(calls.length, 2, 'known group still cached');
});

const BOT = '15550100777@s.whatsapp.net';
const PARTNER_PHONE = '15550100002';
const member = (phone) => ({ id: `${phone}@s.whatsapp.net` });

test('membersAreAllAllowed is true only when every member is allowed or is the bot', () => {
  const allowed = parseAllowedUsers(`${OWNER_PHONE},${PARTNER_PHONE}`);
  const family = [member(OWNER_PHONE), member(PARTNER_PHONE), { id: BOT }];
  assert.equal(membersAreAllAllowed(family, allowed, [BOT], '/nonexistent'), true);
  assert.equal(membersAreAllAllowed([...family, member('15550100099')], allowed, [BOT], '/nonexistent'), false);
  assert.equal(membersAreAllAllowed([], allowed, [BOT], '/nonexistent'), false);
  assert.equal(membersAreAllAllowed(undefined, allowed, [BOT], '/nonexistent'), false);
});

test('a member listed by LID is matched through its phone number field', () => {
  const allowed = parseAllowedUsers(OWNER_PHONE);
  const byLid = [{ id: `${OWNER_LID}@lid`, phoneNumber: `${OWNER_PHONE}@s.whatsapp.net` }, { id: BOT }];
  assert.equal(membersAreAllAllowed(byLid, allowed, [BOT], '/nonexistent'), true);
});

test('check reports a private group, and a friend joining makes it shared', async () => {
  const metadataByGroup = {
    [GROUP]: { subject: 'Home', owner: `${OWNER_PHONE}@s.whatsapp.net`, participants: [member(OWNER_PHONE), { id: BOT }] },
  };
  const { gate, calls } = gateFor({ metadataByGroup });
  assert.equal((await gate.check(GROUP, [BOT])).membersAllAllowed, true);

  metadataByGroup[GROUP] = { ...metadataByGroup[GROUP], participants: [member(OWNER_PHONE), member('15550100099'), { id: BOT }] };
  assert.equal((await gate.check(GROUP, [BOT])).membersAllAllowed, true, 'cached until WhatsApp reports the change');

  gate.forget(GROUP);
  const after = await gate.check(GROUP, [BOT]);
  assert.equal(after.admitted, true);
  assert.equal(after.membersAllAllowed, false);
  assert.equal(calls.length, 2);
});

test('a group created by someone else is never reported as private', async () => {
  const { gate } = gateFor({
    metadataByGroup: { [GROUP]: { subject: 'x', owner: `${STRANGER_LID}@lid`, participants: [member(OWNER_PHONE), { id: BOT }] } },
  });
  assert.deepEqual(await gate.check(GROUP, [BOT]), {
    admitted: false, creatorIds: [STRANGER_LID], subject: 'x', membersAllAllowed: false,
  });
});
