package com.screenassistant;

/** 复盘请求只读取原场次，凭据变化后不得显示迟到结果。 */
final class RecapRequest {
    static boolean sameSession(LiveClient.Session a, LiveClient.Session b) {
        return a != null && b != null && a.id.equals(b.id)
                && a.baseUrl.equals(b.baseUrl) && a.token.equals(b.token);
    }
    static boolean mayApply(LiveClient.Session requested, LiveClient.Session current, boolean cached) {
        return sameSession(requested, current) || (cached && current == null);
    }
    static String failure(Exception e) {
        if (e instanceof LiveClient.HttpError) {
            int code = ((LiveClient.HttpError)e).status;
            if (code == 401 || code == 403) return "本场复盘读取未确认，权限可能已失效（HTTP " + code + "）";
        }
        return "本场复盘读取未确认：" + e.getClass().getSimpleName();
    }
}
