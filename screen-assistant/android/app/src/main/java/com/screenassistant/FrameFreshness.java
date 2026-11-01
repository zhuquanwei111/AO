package com.screenassistant;

/** 按单调时钟限制观察的时效，不跨暂停或场次继续发送旧帧。 */
final class FrameFreshness {
    static boolean valid(boolean active, long frameUptime, long earliest, long now) {
        return active && frameUptime >= earliest && now >= frameUptime && now-frameUptime < 6500;
    }
}
