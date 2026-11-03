package com.screenassistant;

import java.util.ArrayList;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/** 保留换号时尚未回来的复盘；结果到达后不再保存该条凭证。 */
final class RecapHistory {
    private RecapHistory() { }
    static JSONArray remember(JSONArray history, LiveClient.Session session) throws JSONException {
        JSONArray copy = new JSONArray(history.toString());
        if (session == null || !session.stopped) return copy;
        for (int i = 0; i < copy.length(); i++) {
            JSONObject row = copy.getJSONObject(i), saved = row.optJSONObject("session");
            if (saved != null && RecapRequest.sameSession(session, LiveClient.Session.from(saved))) return copy;
            if (session.id.equals(row.optString("session_id")) && session.baseUrl.equals(row.optString("base_url"))) return copy;
        }
        return copy.put(new JSONObject().put("session", session.json()));
    }
    static List<LiveClient.Session> pending(JSONArray history) throws JSONException {
        List<LiveClient.Session> pending = new ArrayList<>();
        for (int i = 0; i < history.length(); i++) {
            JSONObject saved = history.getJSONObject(i).optJSONObject("session");
            if (saved != null) pending.add(LiveClient.Session.from(saved));
        }
        return pending;
    }
    static JSONArray complete(JSONArray history, LiveClient.Session session, JSONObject summary, JSONArray events) throws JSONException {
        JSONArray copy = new JSONArray(history.toString());
        if (!session.id.equals(summary.optString("session_id")) || !RecapText.terminal(summary)) return copy;
        for (int i = 0; i < copy.length(); i++) {
            JSONObject saved = copy.getJSONObject(i).optJSONObject("session");
            if (saved != null && RecapRequest.sameSession(session, LiveClient.Session.from(saved))) {
                copy.put(i, new JSONObject().put("session_id", session.id).put("base_url", session.baseUrl)
                        .put("summary", summary).put("events", events == null ? JSONObject.NULL : events));
                break;
            }
        }
        return copy;
    }
}
