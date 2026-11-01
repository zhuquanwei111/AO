package com.screenassistant;

import org.junit.Test;
import static org.junit.Assert.*;
import org.json.JSONObject;

public class RecapRequestTest {
    @Test public void sameSessionCanRefreshLateResultButChangedCredentialsCannotApply() throws Exception {
        LiveClient.Session old=new LiveClient.Session("http://localhost", "old", "token", 1, true);
        LiveClient.Session newer=new LiveClient.Session("http://localhost", "new", "token", 2, false);
        JSONObject summary=new JSONObject().put("stopped_at_ms",1).put("backstage_recap",new JSONObject().put("status","not_connected"));
        assertFalse(RecapText.pending(summary)); assertFalse(RecapText.terminal(summary));
        assertTrue(RecapRequest.mayApply(old,old,false));
        summary.getJSONObject("backstage_recap").put("status","captured_locally");
        assertTrue(RecapText.terminal(summary));
        assertFalse(RecapRequest.mayApply(old,newer,false));
        assertFalse(RecapRequest.mayApply(old,newer,true));
        assertTrue(RecapRequest.mayApply(old,null,true));
    }
    @Test public void forbiddenIsReportedWithoutClaimingRecovery() {
        String message=RecapRequest.failure(new LiveClient.HttpError(403));
        assertTrue(message.contains("读取未确认")); assertTrue(message.contains("权限可能已失效"));
        assertTrue(message.contains("403"));
    }
}
