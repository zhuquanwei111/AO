package com.screenassistant;

import org.json.JSONObject;
import org.junit.Test;
import java.net.ServerSocket;
import java.net.Socket;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.Assert.*;

public class LiveClientTest {
    @Test public void urlRejectsCredentialsQueryAndBadPort() {
        assertTrue(LiveClient.validBaseUrl("http://192.168.1.10:8000/prefix/"));
        for (String url : new String[]{"http://u:p@localhost", "http://localhost?x=1", "file:///tmp/x",
                "http://localhost:0", "http://localhost/#x", "http://local host"}) {
            assertFalse(url, LiveClient.validBaseUrl(url));
        }
    }

    @Test public void usesBoundBasePathAndSessionTokenAndPagesEvents() throws Exception {
        ServerSocket server = new ServerSocket(0, 2, java.net.InetAddress.getByName("127.0.0.1"));
        AtomicReference<String> token = new AtomicReference<>();
        AtomicReference<Throwable> error = new AtomicReference<>();
        Thread responder = new Thread(() -> {
            try {
              for (int i = 0; i < 2; i++) try (Socket socket = server.accept()) {
                BufferedReader reader = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8));
                String request = reader.readLine();
                assertTrue(request.startsWith("GET /prefix/v1/live/sessions/id/events?"));
                String line;
                while ((line = reader.readLine()) != null && !line.isEmpty()) {
                    if (line.toLowerCase(java.util.Locale.ROOT).startsWith("x-live-session-token:")) token.set(line.split(":", 2)[1].trim());
                }
                byte[] bytes = (i == 1 ? "{\"events\":[{\"phenomenon\":\"第二条\"}],\"has_more\":false}"
                    : "{\"events\":[{\"phenomenon\":\"第一条\"}],\"has_more\":true,\"next_cursor\":\"next\"}")
                    .getBytes(StandardCharsets.UTF_8);
                socket.getOutputStream().write(("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                        + bytes.length + "\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
                socket.getOutputStream().write(bytes);
              }
            } catch (Throwable e) { error.set(e); }
        });
        responder.start();
        try {
            LiveClient.Session s = new LiveClient.Session("http://127.0.0.1:" + server.getLocalPort()
                    + "/prefix/", "id", "private-token", 10L, false);
            assertEquals(2, LiveClient.events(s).length());
            assertEquals("private-token", token.get());
            assertTrue(LiveClient.Session.from(s.asStopped().json()).stopped);
            responder.join(2000); assertNull(error.get());
        } finally { server.close(); }
    }

    @Test public void cleansMarkdownWithoutDeletingNumbersOrFencedBody() {
        assertEquals("1. 200 **原文**",DisplayText.observed(" 1.  200 **原文** "));
        assertEquals("运营\n保持 123 人", DisplayText.labeled("运营", "# **保持** [123 人](https://x)"));
        assertEquals("正文 继续", DisplayText.plain("```text\n正文\n```\n- 继续"));
        assertEquals("问答\n你好", DisplayText.labeled("问答", "*你好*"));
        assertEquals("", DisplayText.labeled("运营", "  "));
    }

    @Test public void nestedMarkdownIsPlainAndPurposeSwitchIsExplicit() {
        assertEquals("运营\n建议 保持节奏", DisplayText.labeled("运营", "> ## **建议**\n> - 保持节奏"));
        assertEquals("问答\n继续", DisplayText.labeled("问答", "~~继续~~"));
        assertEquals("建议 操作 保持节奏 看向镜头", DisplayText.plain("| 建议 | 操作 |\n| --- | :---: |\n| 保持节奏 | 看向镜头 |\n***"));
        String bounded = DisplayText.plain("😀".repeat(700));
        assertEquals(601, bounded.codePointCount(0, bounded.length()));
        assertTrue(bounded.endsWith("…"));
    }
}
