"""Groups admitted by their creator (``group_allow_creators``) instead of by listed group JID."""

from gateway.config import Platform, PlatformConfig

OWNER = "15550100001@s.whatsapp.net"
PARTNER = "15550100002@s.whatsapp.net"
FRIEND = "15550100099@s.whatsapp.net"
LISTED_GROUP = "120363000000000001@g.us"
NEW_GROUP = "120363000000000002@g.us"


def _adapter(group_policy="allowlist", creators=(OWNER, PARTNER), allow_from=(OWNER, PARTNER), require_mention=False):
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter

    adapter = object.__new__(WhatsAppAdapter)
    adapter.platform = Platform.WHATSAPP
    adapter.config = PlatformConfig(enabled=True, extra={"require_mention": require_mention})
    adapter._dm_policy = "allowlist"
    adapter._dm_allowlist_source = "config"
    adapter._allow_from = set(allow_from)
    adapter._group_policy = group_policy
    adapter._group_allow_from = {LISTED_GROUP}
    adapter._group_allow_creators = set(creators)
    adapter._mention_patterns = adapter._compile_mention_patterns()
    return adapter


def _message(chat=NEW_GROUP, sender=OWNER, creator_ids=("15550100001",), body="hello", private=False):
    return {"isGroup": True, "chatId": chat, "senderId": sender, "body": body,
            "groupCreatorIds": list(creator_ids), "groupMembersAllAllowed": private,
            "botIds": [], "mentionedIds": []}


def test_unlisted_group_created_by_an_allowed_person_is_processed():
    assert _adapter()._should_process_message(_message()) is True


def test_partner_speaking_in_a_group_the_owner_created_is_processed():
    assert _adapter()._should_process_message(_message(sender=PARTNER)) is True


def test_friend_in_a_creator_admitted_group_is_not_answered():
    assert _adapter()._should_process_message(_message(sender=FRIEND)) is False


def test_group_created_by_someone_else_is_rejected_even_for_the_owner():
    assert _adapter()._should_process_message(_message(creator_ids=("15550100099",))) is False


def test_missing_creator_is_rejected():
    assert _adapter()._should_process_message(_message(creator_ids=())) is False


def test_no_creator_setting_keeps_the_plain_allowlist_behaviour():
    adapter = _adapter(creators=())
    assert adapter._should_process_message(_message()) is False
    assert adapter._should_process_message(_message(chat=LISTED_GROUP, creator_ids=())) is True


def test_wildcard_creator_never_admits():
    assert _adapter(creators=("*",))._should_process_message(_message()) is False


def test_creators_do_not_open_groups_under_a_disabled_policy():
    assert _adapter(group_policy="disabled")._should_process_message(_message()) is False


def test_listed_group_still_answers_everyone_in_it():
    assert _adapter()._should_process_message(_message(chat=LISTED_GROUP, sender=FRIEND, creator_ids=())) is True


def test_mention_rule_still_applies_in_a_creator_admitted_group():
    adapter = _adapter(require_mention=True)
    adapter.config.extra["mention_patterns"] = [r"(?i)\bluna\b"]
    adapter._mention_patterns = adapter._compile_mention_patterns()
    assert adapter._should_process_message(_message(body="what's for dinner")) is False
    assert adapter._should_process_message(_message(body="Luna, what's for dinner")) is True


def test_adapter_reads_the_setting_and_passes_it_to_the_bridge():
    from plugins.platforms.whatsapp import adapter as wa

    built = wa.WhatsAppAdapter(PlatformConfig(enabled=True, extra={"group_allow_creators": [OWNER, PARTNER]}))
    assert built._group_allow_creators == {OWNER, PARTNER}
    assert "WHATSAPP_GROUP_ALLOW_CREATORS" in wa._BRIDGE_PASSTHROUGH_ENV


def test_private_creator_group_answers_without_a_mention():
    adapter = _adapter(require_mention=True)
    assert adapter._should_process_message(_message(body="what's for dinner", private=True)) is True


def test_private_flag_alone_does_not_admit_a_group_someone_else_created():
    adapter = _adapter(require_mention=True)
    assert adapter._should_process_message(_message(creator_ids=("15550100099",), private=True)) is False


def test_private_flag_does_not_bypass_the_mention_rule_in_a_listed_group():
    adapter = _adapter(require_mention=True)
    listed = _message(chat=LISTED_GROUP, creator_ids=(), body="what's for dinner", private=True)
    assert adapter._should_process_message(listed) is False
