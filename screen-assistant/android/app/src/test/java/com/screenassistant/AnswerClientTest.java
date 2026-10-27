package com.screenassistant;

import org.junit.Test;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.io.ByteArrayOutputStream;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.Assert.*;

public class AnswerClientTest {
    @Test public void legacySuccessDuplicateAndDropKeepExistingContract() throws Exception {
        ServerSocket server=new ServerSocket(0,3,java.net.InetAddress.getByName("127.0.0.1"));
        AtomicReference<Throwable> failure=new AtomicReference<>();
        Thread response=new Thread(() -> {
            try {
                for (int i=0;i<3;i++) try (Socket socket=server.accept()) {
                    socket.setSoTimeout(3000);
                    ByteArrayOutputStream headers=new ByteArrayOutputStream(); int value;
                    while ((value=socket.getInputStream().read())!=-1) {
                        headers.write(value);
                        if (headers.toString("US-ASCII").endsWith("\r\n\r\n")) break;
                    }
                    String h=headers.toString("US-ASCII");
                    assertTrue(h.startsWith("POST /prefix/v1/answer HTTP/1.1"));
                    int length=0;
                    for (String line:h.split("\r\n")) if (line.toLowerCase(java.util.Locale.ROOT).startsWith("content-length:"))
                        length=Integer.parseInt(line.split(":",2)[1].trim());
                    ByteArrayOutputStream body=new ByteArrayOutputStream();
                    for (int j=0;j<length;j++) body.write(socket.getInputStream().read());
                    assertEquals("hello",new org.json.JSONObject(body.toString("UTF-8")).getString("sentence"));
                    byte[] bytes=(i==0?"{\"status\":\"ok\",\"reply\":\"你好\"}":"").getBytes(StandardCharsets.UTF_8);
                    int code=i==0?200:i==1?204:503;
                    socket.getOutputStream().write(("HTTP/1.1 "+code+" Test\r\nX-Request-ID: regression-id\r\nContent-Length: "
                            +bytes.length+"\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
                    socket.getOutputStream().write(bytes);
                }
            } catch (Throwable e) { failure.set(e); }
        }); response.start();
        try {
            String base="http://127.0.0.1:"+server.getLocalPort()+"/prefix/";
            AnswerClient.Result success=AnswerClient.post(base,"hello",1000,3000);
            assertEquals(AnswerClient.Outcome.SUCCESS,success.outcome); assertEquals("你好",success.reply);
            assertEquals("regression-id",success.requestId);
            assertEquals(AnswerClient.Outcome.DUPLICATE,AnswerClient.post(base,"hello",1000,3000).outcome);
            assertEquals(AnswerClient.Outcome.DROP,AnswerClient.post(base,"hello",1000,3000).outcome);
            response.join(4000); assertNull(failure.get());
        } finally { server.close(); }
    }
}
