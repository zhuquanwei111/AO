package com.screenassistant;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;

public class RecapHistoryTest {
    private LiveClient.Session scene(String id, String token) {
        return new LiveClient.Session("http://fixture", id, token, 100, true);
    }
    private JSONObject result(String id) throws Exception {
        return new JSONObject().put("session_id", id).put("stopped_at_ms", 200)
                .put("backstage_recap", new JSONObject().put("status", "partial"));
    }
    @Test public void changingAccountsKeepsBothPendingSceneCredentialsWithoutDuplicates() throws Exception {
        JSONArray history = RecapHistory.remember(new JSONArray(), scene("A", "token-A"));
        history = RecapHistory.remember(history, scene("B", "token-B"));
        history = RecapHistory.remember(history, scene("A", "token-A"));
        assertEquals(2, RecapHistory.pending(history).size());
        assertEquals("A", RecapHistory.pending(history).get(0).id);
        assertEquals("B", RecapHistory.pending(history).get(1).id);
    }
    @Test public void lateResultKeepsItsSceneAndRemovesOnlyItsOwnCredential() throws Exception {
        JSONArray history = RecapHistory.remember(new JSONArray(), scene("A", "token-A"));
        history = RecapHistory.remember(history, scene("B", "token-B"));
        history = RecapHistory.complete(history, scene("A", "token-A"), result("A"), new JSONArray());
        assertEquals(1, RecapHistory.pending(history).size());
        assertEquals("B", RecapHistory.pending(history).get(0).id);
        assertFalse(history.toString().contains("token-A"));
        assertEquals("A", history.getJSONObject(0).getJSONObject("summary").getString("session_id"));
    }
    @Test public void wrongSessionOrCredentialCannotOverwriteArchivedResult() throws Exception {
        JSONArray history = RecapHistory.remember(new JSONArray(), scene("A", "token-A"));
        String before = history.toString();
        assertEquals(before, RecapHistory.complete(history, scene("A", "other-token"), result("A"), new JSONArray()).toString());
        assertEquals(before, RecapHistory.complete(history, scene("A", "token-A"), result("B"), new JSONArray()).toString());
        assertEquals(before, history.toString());
    }
    @Test public void oneReadFailureMustRemainEligibleForAutomaticRefresh() throws Exception {
        assertTrue(RecapText.shouldRefresh(null));
        assertTrue(RecapText.shouldRefresh(new JSONObject().put("backstage_recap",
                new JSONObject().put("status", "capturing"))));
        assertFalse(RecapText.shouldRefresh(result("A")));
    }
}
