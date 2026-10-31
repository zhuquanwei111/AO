from copy import deepcopy

from screen_assistant_server.comment_reconstruction import reconstruct_comments


def row(event_id, text, box, *, batch="b", at=1000, index=0):
    x, y, w, h = box
    return {"event_id": event_id, "observed_at_ms": at, "confidence": 0.9,
            "capture_batch_id": batch, "line_index": index,
            "content": {"text": text, "text_position": {"x": x, "y": y, "width": w, "height": h}}}


def test_badge_sample_preserves_ambiguous_badges_but_recovers_text():
    rows = [row("a", "049", (69/925, 47/128, 61/925, 35/128)),
            row("b", "1爱哄人", (155/925, 44/128, 135/925, 38/128), index=1),
            row("c", "紫菜蛋花汤：bless you", (384/925, 40/128, 459/925, 56/128), index=2)]
    comments = reconstruct_comments(rows)
    comment = next(c for c in comments if c["text"] == "bless you")
    assert comment["nickname"] == "紫菜蛋花汤"
    assert comment["consumption_level"] is None
    assert comment["fan_level"] is None
    assert comment["fan_group"] is None
    assert "c" in comment["source_event_ids"]


def test_complete_batch_can_use_real_badge_body_without_guessing_levels():
    rows = [row("a", "049", (69/925, 47/128, 61/925, 35/128)),
            row("b", "1爱哄人", (155/925, 44/128, 135/925, 38/128), index=1),
            row("c", "紫菜蛋花汤：bless you", (384/925, 40/128, 459/925, 56/128), index=2)]
    comment = next(c for c in reconstruct_comments(rows, batch_complete=True, quality_gaps=[])
                   if c["text"] == "bless you")
    assert comment["field_status"]["text"] == "usable"
    assert comment["boundary_status"] == "locally_complete"
    assert comment["field_sources"]["text"] == ["c"]
    assert comment["fan_level"] is None and comment["consumption_level"] is None


def test_clear_question_requires_explicit_complete_batch_and_quality_review():
    rows = [row("a", "阿青：今天几点开播？", (.1, .3, .45, .04))]
    eligible = reconstruct_comments(rows, batch_complete=True, quality_gaps=[])[0]
    assert eligible["field_status"]["text"] == "usable"
    assert eligible["field_status"]["nickname"] == "usable"
    assert eligible["boundary_status"] == "locally_complete"
    assert reconstruct_comments(rows)[0]["field_status"]["text"] == "candidate"
    assert reconstruct_comments(rows, batch_complete=True)[0]["field_status"]["text"] == "candidate"


def test_unreliable_neighbour_or_unlocated_gap_blocks_upgrade():
    rows = [row("a", "阿青：今天几点开播？", (.1, .3, .45, .04))]
    for gaps in ([{"event_id": "gap", "text_position": None}],
                 [{"event_id": "gap", "text_position": {"x": .1, "y": .345, "width": .2, "height": .04}}]):
        comment = reconstruct_comments(rows, batch_complete=True, quality_gaps=gaps)[0]
        assert comment["field_status"]["text"] != "usable"
        assert comment["boundary_status"] != "locally_complete"
        assert comment["usability_reasons"]


def test_unassigned_continuation_or_truncated_text_blocks_upgrade():
    rows = [row("a", "阿青：这是长评论的开头", (.1, .3, .45, .04)),
            row("b", "后面仍然有不少内容", (.1, .345, .4, .04), index=1)]
    comment = next(c for c in reconstruct_comments(rows, batch_complete=True, quality_gaps=[])
                   if c["text"] is not None)
    assert comment["field_status"]["text"] != "usable"
    truncated = reconstruct_comments([row("c", "阿青：还有更多…", (.1, .3, .3, .04))],
                                     batch_complete=True, quality_gaps=[])[0]
    assert truncated["field_status"]["text"] != "usable"
    cropped = reconstruct_comments([row("d", "不补：对看的", (86/576, 13/580, 116/576, 14/580))],
                                   batch_complete=True, quality_gaps=[])[0]
    assert cropped["field_status"]["text"] != "usable"
    assert "near_top_edge" in cropped["usability_reasons"]


def test_real_a30_long_comment_with_unassigned_continuation_is_not_usable():
    rows = [row("a", "别忽悠我就行：点点关注点点", (78/576, 45/580, 266/576, 23/580)),
            row("b", "赞，亮个粉丝牌！", (31/576, 72/580, 150/576, 26/580), index=1)]
    comment = next(c for c in reconstruct_comments(rows, batch_complete=True, quality_gaps=[])
                   if c["text"] is not None)
    assert comment["text"] == "点点关注点点"
    assert comment["field_status"]["text"] == "candidate"
    assert "nearby_unassigned" in comment["usability_reasons"]


def test_unlocated_observation_and_multiple_batches_cannot_claim_complete():
    first = row("a", "阿青：今天几点开播？", (.1, .3, .45, .04))
    missing = row("b", "不明片段", (.1, .4, .2, .04), index=1)
    missing["content"]["text_position"] = None
    result = reconstruct_comments([first, missing], batch_complete=True, quality_gaps=[])
    assert result[0]["field_status"]["text"] != "usable"
    second = row("c", "小林：你好", (.1, .6, .3, .04), batch="other")
    assert all(c["field_status"]["text"] != "usable" for c in
               reconstruct_comments([first, second], batch_complete=True, quality_gaps=[]))


def test_numeric_header_does_not_block_usable_body_or_invent_nickname():
    comment = reconstruct_comments([row("a", "10交换余生：小匪宝宝", (.1, .3, .4, .04))],
                                   batch_complete=True, quality_gaps=[])[0]
    assert comment["field_status"]["text"] == "usable"
    assert comment["nickname"] is None
    assert comment["field_status"]["nickname"] == "unknown"
    assert comment["field_reasons"]["nickname"] == ["ambiguous_header"]


def test_decorated_header_is_not_used_as_a_confirmed_nickname():
    comment = reconstruct_comments([row("a", "●14梦宝：小匪宝宝", (.1, .3, .4, .04))],
                                   batch_complete=True, quality_gaps=[])[0]
    assert comment["field_status"]["text"] == "usable"
    assert comment["field_status"]["nickname"] != "usable"
    assert comment["field_reasons"]["nickname"] == ["ambiguous_header"]


def test_output_does_not_alias_observations_or_previous():
    observations = [row("a", "阿青：你好", (.1, .5, .3, .04))]
    original = deepcopy(observations)
    previous = reconstruct_comments(observations)
    saved_previous = deepcopy(previous)
    new = reconstruct_comments([row("b", "阿青：你好", (.1, .3, .3, .04), at=2000)], previous)
    assert previous == saved_previous
    previous[0]["fragments"][0]["position"]["x"] = 0
    assert observations == original
    assert new[0]["first_observed_at_ms"] == 2000
    observations[0]["content"]["text_position"]["y"] = 0
    assert previous[0]["fragments"][0]["position"]["y"] == .5
    current_rows = [row("c", "阿青：你好", (.1, .3, .3, .04), at=3000)]
    current_snapshot = deepcopy(current_rows)
    old_snapshot = deepcopy(saved_previous)
    output = reconstruct_comments(current_rows, saved_previous)
    output[0]["fragments"][0]["position"]["x"] = 0
    assert current_rows == current_snapshot
    assert saved_previous == old_snapshot


def test_left_shifted_continuation_and_right_advertisement():
    rows = [row("a", "19", (35/576, 157/580, 44/576, 16/580)),
            row("b", "高冷男神互动就摆：", (81/576, 154/580, 175/576, 23/580), index=1),
            row("c", "我的我的", (261/576, 157/580, 80/576, 19/580), index=2),
            row("d", "我的", (29/576, 182/580, 46/576, 25/580), index=3),
            row("e", "5老默：我她", (34/576, 225/580, 149/576, 19/580), index=4),
            row("f", "角色扮演", (393/576, 155/580, 62/576, 18/580), index=5)]
    comments = reconstruct_comments(rows)
    target = next(c for c in comments if c["nickname"] == "高冷男神互动就摆")
    assert target["text"] == "我的我的我的"
    assert "f" not in target["source_event_ids"]
    assert target["field_sources"]["nickname"] == ["b"]
    assert target["field_sources"]["text"] == ["c", "d"]


def test_no_batch_never_merges_and_same_text_different_names_remains_separate():
    comments = reconstruct_comments([
        row("a", "交换余生：小匪宝宝", (.1, .1, .35, .04), batch=None),
        row("b", "梦宝不是梦：小匪宝宝", (.1, .17, .4, .04), batch=None, index=1)])
    assert len(comments) == 2
    assert {c["nickname"] for c in comments} == {"交换余生", "梦宝不是梦"}


def test_real_b15_same_text_two_rows_remain_separate():
    comments = reconstruct_comments([
        row("a", "10交换余生：小匪宝宝", (34/576, 418/580, 232/576, 23/580)),
        row("b", "14", (34/576, 462/580, 47/576, 18/580), index=1),
        row("c", "梦宝不是梦：小匪宝宝", (74/576, 462/580, 214/576, 22/580), index=2)])
    matches = [c for c in comments if c["text"] == "小匪宝宝"]
    assert len(matches) == 2
    assert len({c["comment_id"] for c in matches}) == 2
    assert {c["nickname"] for c in matches} == {None, "梦宝不是梦"}


def test_same_batch_id_from_different_sources_is_never_grouped():
    a = row("a", "阿青：", (.1, .1, .2, .04))
    b = row("b", "你好", (.31, .1, .1, .04), index=1)
    a["source"], b["source"] = "android", "ios"
    comments = reconstruct_comments([a, b])
    assert len(comments) == 2
    assert all(len(c["source_event_ids"]) == 1 for c in comments)


def test_cross_frame_candidates_from_different_sources_do_not_share_track():
    a = row("a", "阿青：你好", (.1, .5, .3, .04))
    b = row("b", "阿青：你好", (.1, .2, .3, .04), at=2000)
    a["source"], b["source"] = "android", "ios"
    first = reconstruct_comments([a])
    second = reconstruct_comments([b], first)
    assert second[0]["comment_id"] != first[0]["comment_id"]


def test_single_matching_line_does_not_prove_continuity():
    first = reconstruct_comments([row("a", "魔法冰城：打野不带节奏靠队友", (.06, .48, .72, .11))])
    second = reconstruct_comments([row("b", "魔法冰城：打野不带节奏靠队友", (.06, .12, .72, .11), at=4000)], first)
    assert second[0]["comment_id"] != first[0]["comment_id"]
    assert second[0]["tracking_status"] == "ambiguous_single"


def test_neighbouring_comments_require_consistent_order_and_movement():
    first = reconstruct_comments([row("a", "阿青：你好", (.1, .5, .3, .04)),
                                  row("b", "小林：再见", (.1, .6, .3, .04), index=1)])
    second = reconstruct_comments([row("c", "阿青：你好", (.1, .3, .3, .04), at=2000),
                                   row("d", "小林：再见", (.1, .4, .3, .04), at=2000, index=1)], first)
    assert [c["comment_id"] for c in second] == [c["comment_id"] for c in first]
    inconsistent = reconstruct_comments([row("e", "阿青：你好", (.1, .3, .3, .04), at=2000),
                                         row("f", "小林：再见", (.1, .5, .3, .04), at=2000, index=1)], first)
    assert all(c["tracking_status"] == "ambiguous_single" for c in inconsistent)


def test_same_input_produces_same_temporary_id():
    observation = row("a", "阿青：你好", (.1, .5, .3, .04))
    assert reconstruct_comments([observation]) == reconstruct_comments([observation])


def test_untrusted_colon_and_missing_geometry_are_not_guessed():
    comments = reconstruct_comments([row("a", "https://example.com", (.1, .1, .3, .04), batch=None)])
    assert not any(c["nickname"] == "https" for c in comments)


def test_pinned_reply_is_not_inferred_as_viewer_comment():
    comments = reconstruct_comments([row("a", "主播回复Max.：后面的书有在看吗", (.04, .4, .7, .04))])
    assert all(c["text"] is None for c in comments)


def test_normalized_screen_aspect_does_not_block_adjacent_text_fragments():
    comments = reconstruct_comments([
        row("a", "高冷男神互动就摆：", (.14, .50, .30, .018)),
        row("b", "我的我的", (.475, .50, .14, .018), index=1)])
    assert next(c for c in comments if c["nickname"] == "高冷男神互动就摆")["text"] == "我的我的"


def test_next_numeric_badge_is_not_a_continuation():
    comments = reconstruct_comments([
        row("a", "豆豆ovo：人妻", (.14, .648, .25, .038)),
        row("b", "016", (.057, .721, .08, .034), index=1),
        row("c", "第三極：", (.20, .722, .127, .036), index=2),
        row("d", "我了鬼老婆", (.335, .724, .18, .036), index=3)])
    assert next(c for c in comments if c["nickname"] == "豆豆ovo")["text"] == "人妻"


def test_next_unseparated_name_is_not_a_continuation():
    comments = reconstruct_comments([
        row("a", "七七：到了到了", (82/576, 505/580, 140/576, 23/580)),
        row("b", "11认真的薛1来了", (34/576, 547/580, 180/576, 22/580), index=1)])
    assert next(c for c in comments if c["nickname"] == "七七")["text"] == "到了到了"


def test_duplicate_same_content_in_current_batch_cannot_share_track():
    first = reconstruct_comments([row("a", "阿青：你好", (.1, .5, .3, .04))])
    second = reconstruct_comments([
        row("b", "阿青：你好", (.1, .3, .3, .04), at=2000),
        row("c", "阿青：你好", (.1, .37, .3, .04), at=2000, index=1)], first)
    assert len({item["comment_id"] for item in second}) == 2
    assert all(item["tracking_status"] != "continuous_candidate" for item in second)


def test_video_27_and_30_unclassified_numeric_prefix_can_track_as_candidate():
    first = reconstruct_comments([
        row("a", "6魔法冰城（魔法学院店）：打野不带节奏靠队", (24/432, 84/172, 327/432, 18/172)),
        row("b", "友", (23/432, 108/172, 18/432, 16/172), index=1)])
    target = next(c for c in first if c["text"] == "打野不带节奏靠队友")
    second = reconstruct_comments([
        row("c", "6魔法冰城（魔法学院店）：打野不带节奏靠队", (23/432, 20/172, 329/432, 19/172), at=4000),
        row("d", "友", (23/432, 42/172, 18/432, 17/172), at=4000, index=1)], first)
    target2 = next(c for c in second if c["text"] == "打野不带节奏靠队友")
    assert target2["nickname"] is None
    assert target2["comment_id"] == target["comment_id"]
    assert target2["first_observed_at_ms"] == 1000
    assert set(target2["source_event_ids"]) == {"a", "b", "c", "d"}
