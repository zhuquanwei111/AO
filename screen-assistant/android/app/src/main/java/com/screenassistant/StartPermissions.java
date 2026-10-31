package com.screenassistant;

/** 每次用户点击的一次权限旅程；通知/麦克风拒绝后仍可申请屏幕授权。 */
final class StartPermissions {
    enum Step { OVERLAY, NOTIFICATIONS, MICROPHONE, PROJECTION }
    private boolean notificationsAsked, microphoneAsked;

    void reset() { notificationsAsked = false; microphoneAsked = false; }

    Step next(boolean overlayAllowed, boolean needsNotifications, boolean needsMicrophone) {
        if (!overlayAllowed) return Step.OVERLAY;
        if (needsNotifications && !notificationsAsked) {
            notificationsAsked = true;
            return Step.NOTIFICATIONS;
        }
        if (needsMicrophone && !microphoneAsked) {
            microphoneAsked = true;
            return Step.MICROPHONE;
        }
        return Step.PROJECTION;
    }
}
