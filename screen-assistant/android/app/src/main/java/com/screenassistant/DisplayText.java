package com.screenassistant;

/** 两种真实用途均整条替换；纯文本归一化后的相同内容不重新显示。 */
public final class DisplayText {
    private DisplayText() {}
    public static String observed(String raw) { return raw == null ? "" : raw.trim().replaceAll("\\s+", " "); }
    public static String plain(String raw) {
        if (raw == null) return "";
        String value = raw.replaceAll("(?m)^\\s*(?:`{3,}|~{3,})[^\\n]*$", "")
                .replaceAll("!?(?:\\[([^]\\n]+)\\])\\([^\\n)]*\\)", "$1")
                .replaceAll("(?m)^\\s*(?:#{1,6}\\s+|>\\s*|[-*+]\\s+|\\d+[.)]\\s+)+", "")
                .replace("**", "").replace("__", "").replace("~~", "").replace("`", "")
                .replaceAll("(?<!\\w)[*_]([^*_\\n]+)[*_](?!\\w)", "$1")
                .replaceAll("\\s+", " ").trim();
        int length = value.codePointCount(0, value.length());
        return length <= 600 ? value : value.substring(0, value.offsetByCodePoints(0, 600)) + "…";
    }

    public static String labeled(String purpose, String raw) {
        String value = plain(raw);
        return value.isEmpty() ? "" : purpose + "\n" + value;
    }
}
