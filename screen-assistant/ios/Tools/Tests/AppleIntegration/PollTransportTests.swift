import XCTest
@testable import ScreenAssistant

/// 每个测试使用私有 session，fixture 由路径决定，不注册全局 URLProtocol。
private final class PollFixtureProtocol: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool {
        request.url?.host == "poll-fixture.invalid"
    }

    override class func canInit(with task: URLSessionTask) -> Bool {
        guard let request = task.currentRequest else { return false }
        return canInit(with: request)
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let path = request.url!.path
        if path.hasSuffix("/stop"),
           (!path.contains("/sessions/fixture-session/stop") ||
            request.value(forHTTPHeaderField: "X-Live-Session-Token") != "fixture-token") {
            client?.urlProtocol(self, didFailWithError: URLError(.userAuthenticationRequired))
            return
        }
        if path.hasPrefix("/hold/") { return }
        if path.hasPrefix("/timeout/") {
            client?.urlProtocol(self, didFailWithError: URLError(.timedOut))
            return
        }
        if path.hasPrefix("/disconnect/") {
            client?.urlProtocol(self, didFailWithError: URLError(.cannotConnectToHost))
            return
        }
        let status = path.hasPrefix("/http-error/") ? 503 : 200
        let response = HTTPURLResponse(url: request.url!, statusCode: status,
                                       httpVersion: "HTTP/1.1", headerFields: nil)!
        let timing: String
        if path.hasPrefix("/invalid-timing/") {
            timing = "\"server_time_ms\":\"invalid\",\"server_processing_ms\":0"
        } else if path.hasPrefix("/float-timing/") {
            timing = "\"server_time_ms\":1000.0,\"server_processing_ms\":0"
        } else {
            timing = "\"server_time_ms\":1000,\"server_processing_ms\":0"
        }
        let promptFields = "\"input_mode\":\"live_observation\",\"start_at_ms\":1,\"end_at_ms\":1,\"phenomenon\":\"x\",\"suggestion\":\"x\",\"display_text\":\"x\",\"observation_count\":1"
        let prompts = path.hasPrefix("/mixed-timing/")
            ? "[{\"prompt_id\":\"a\",\(promptFields),\"expires_at_ms\":5000},{\"prompt_id\":\"bad\",\(promptFields),\"expires_at_ms\":9.0},{\"prompt_id\":\"b\",\(promptFields),\"expires_at_ms\":6000}]"
            : "[]"
        let json = status == 200
            ? "{\"session_id\":\"fixture-session\",\"prompts\":\(prompts),\"stopped\":false,\"next_cursor\":\"next\",\(timing)}"
            : "{\"detail\":\"fixture\"}"
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(json.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

final class PollTransportTests: XCTestCase {
    func testOnlineCountConfirmationPersistsOnlyForSameRegion() throws {
        let suite = "count-confirmation-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let roi = CGRect(x: 0.1, y: 0.2, width: 0.3, height: 0.4)
        let other = CGRect(x: 0.2, y: 0.2, width: 0.3, height: 0.4)
        XCTAssertFalse(SharedConfig.onlineCountSemanticsConfirmed(defaults: defaults))
        SharedConfig.setOnlineCountSemanticsConfirmed(true, defaults: defaults)
        let restored = try XCTUnwrap(UserDefaults(suiteName: suite))
        XCTAssertTrue(SharedConfig.onlineCountSemanticsConfirmed(defaults: restored))
        XCTAssertTrue(SharedConfig.retainedOnlineCountConfirmation(previous: roi, next: roi, confirmed: true))
        XCTAssertFalse(SharedConfig.retainedOnlineCountConfirmation(previous: roi, next: other, confirmed: true))
        XCTAssertFalse(SharedConfig.retainedOnlineCountConfirmation(previous: roi, next: nil, confirmed: true))
        XCTAssertFalse(SharedConfig.retainedOnlineCountConfirmation(previous: roi, next: roi, confirmed: false))
    }

    func testPromptTypesAndReliableReplyDecodeWithLegacyFallback() throws {
        let prefix = "{\"prompt_id\":\"p\",\"input_mode\":\"live_observation\",\"start_at_ms\":1,\"end_at_ms\":1,\"phenomenon\":\"comment\",\"suggestion\":\"suggestion\",\"display_text\":\"speak\",\"observation_count\":1"
        let typed = prefix + ",\"prompt_type\":\"comment_reply\",\"reply_to\":{\"comment_id\":\"local\",\"nickname\":null,\"text\":\"hello\"}}"
        let reply = try JSONDecoder().decode(LivePrompt.self, from: Data(typed.utf8))
        XCTAssertEqual(reply.promptType, "comment_reply")
        XCTAssertEqual(reply.replyTo?.text, "hello")
        XCTAssertNil(reply.replyTo?.nickname)
        XCTAssertEqual(reply.displayText, "speak")
        let legacy = try JSONDecoder().decode(LivePrompt.self, from: Data((prefix + "}").utf8))
        XCTAssertNil(legacy.promptType)
        XCTAssertEqual(LivePromptDelivery.promptLabel(legacy.promptType), "提示")
    }

    func testCreateCapabilitiesRequireExplicitBooleanTrue() throws {
        let prefix = "{\"session_id\":\"fixture-session\",\"session_token\":\"fixture-token\",\"started_at_ms\":1"
        for (suffix, supported) in [
            ("}", false),
            (",\"capabilities\":{\"observation_batch_metadata\":true}}", true),
            (",\"capabilities\":{\"observation_batch_metadata\":false}}", false),
            (",\"capabilities\":{\"observation_batch_metadata\":1}}", false),
            (",\"capabilities\":\"invalid\"}", false)
        ] {
            let decoded = try JSONDecoder().decode(LiveSessionCredentials.self, from: Data((prefix + suffix).utf8))
            XCTAssertEqual(decoded.capabilities?.observationBatchMetadata == true, supported)
        }
    }

    func testStopPendingCredentialsRoundTripAndLegacyDecode() throws {
        var pending = credentials
        pending.stopPending = true
        let restored = try JSONDecoder().decode(LiveSessionCredentials.self, from: JSONEncoder().encode(pending))
        XCTAssertEqual(restored.sessionID, pending.sessionID)
        XCTAssertEqual(restored.stopPending, true)
        let legacy = Data("{\"session_id\":\"old\",\"session_token\":\"token\",\"started_at_ms\":1}".utf8)
        XCTAssertNil(try JSONDecoder().decode(LiveSessionCredentials.self, from: legacy).stopPending)
    }

    func testStopPendingUsesIsolatedRealCredentialStorage() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let suite = "stop-fixture-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer {
            try? FileManager.default.removeItem(at: directory)
            defaults.removePersistentDomain(forName: suite)
        }
        let url = directory.appendingPathComponent("session.json")
        var pending = credentials
        pending.stopPending = true
        XCTAssertTrue(LiveSessionState.save(pending, fileURL: url, defaults: defaults))
        let restored = try XCTUnwrap(LiveSessionState.credentials(fileURL: url))
        XCTAssertEqual(restored.sessionID, pending.sessionID)
        XCTAssertEqual(restored.token, pending.token)
        XCTAssertEqual(restored.stopPending, true)
        XCTAssertTrue(LiveSessionState.recoverActive(restored, captureEnabled: true))
        XCTAssertTrue(LiveSessionState.recoverActive(restored, captureEnabled: false))
        XCTAssertTrue(LiveSessionState.holdCaptureOnRecovery(restored))
        let invalidURL = directory.appendingPathComponent("missing/session.json")
        XCTAssertFalse(LiveSessionState.save(pending, fileURL: invalidURL, defaults: defaults))
        XCTAssertEqual(LiveSessionState.credentials(fileURL: url)?.stopPending, true)
        XCTAssertTrue(LiveSessionState.confirmStopped(pending, fileURL: url, defaults: defaults))
        XCTAssertEqual(LiveSessionState.credentials(fileURL: url)?.stopPending, false)
        XCTAssertFalse(LiveSessionState.recoverActive(LiveSessionState.credentials(fileURL: url), captureEnabled: false))
    }

    func testStopTransportSuccessAndTimeoutDoNotWriteDiagnosticsBeforeGate() {
        for scenario in ["success", "timeout"] {
            let (client, session) = makeClient(scenario)
            let before = ControlState.serviceConnection()
            let finished = expectation(description: scenario)
            client.stop(credentials, recordResponse: false) { ok, response, error in
                XCTAssertEqual(ok, scenario == "success")
                if scenario == "success" { XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200) }
                else { XCTAssertEqual((error as? URLError)?.code, .timedOut) }
                XCTAssertEqual(ControlState.serviceConnection(), before)
                finished.fulfill()
            }
            wait(for: [finished], timeout: 5)
            session.invalidateAndCancel()
        }
    }

    func testPendingStorageSurvivesTimeoutAndOriginalSessionRetry() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let suite = "stop-retry-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer {
            try? FileManager.default.removeItem(at: directory)
            defaults.removePersistentDomain(forName: suite)
        }
        let url = directory.appendingPathComponent("session.json")
        var pending = credentials
        pending.stopPending = true
        XCTAssertTrue(LiveSessionState.save(pending, fileURL: url, defaults: defaults))
        let (timeoutClient, timeoutSession) = makeClient("timeout")
        let timedOut = expectation(description: "原场结束请求超时")
        timeoutClient.stop(pending, recordResponse: false) { ok, _, _ in
            XCTAssertFalse(ok)
            timedOut.fulfill()
        }
        wait(for: [timedOut], timeout: 5)
        timeoutSession.invalidateAndCancel()
        let restored = try XCTUnwrap(LiveSessionState.credentials(fileURL: url))
        XCTAssertEqual(restored.sessionID, pending.sessionID)
        XCTAssertEqual(restored.token, pending.token)
        XCTAssertEqual(restored.stopPending, true)
        XCTAssertTrue(LiveSessionState.recoverActive(restored, captureEnabled: false))
        XCTAssertTrue(LiveSessionState.holdCaptureOnRecovery(restored))
        let (retryClient, retrySession) = makeClient("success")
        let confirmed = expectation(description: "原场结束重试成功")
        retryClient.stop(restored, recordResponse: false) { ok, _, _ in
            XCTAssertTrue(ok)
            if ok { XCTAssertTrue(LiveSessionState.confirmStopped(restored, fileURL: url, defaults: defaults)) }
            confirmed.fulfill()
        }
        wait(for: [confirmed], timeout: 5)
        retrySession.invalidateAndCancel()
        XCTAssertEqual(LiveSessionState.credentials(fileURL: url)?.stopPending, false)
    }

    private func makeClient(_ scenario: String) -> (LiveOperationsClient, URLSession) {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [PollFixtureProtocol.self]
        let session = URLSession(configuration: configuration)
        let url = URL(string: "https://poll-fixture.invalid/\(scenario)")!
        return (LiveOperationsClient(baseURL: url, session: session)!, session)
    }

    private var credentials: LiveSessionCredentials {
        LiveSessionCredentials(sessionID: "fixture-session", token: "fixture-token", startedAtMS: 0,
                               douyinAccount: nil, backstageTaskID: nil, backstageStatus: nil,
                               backstageStartPending: nil, liveConnectionPending: nil)
    }

    private func check(_ scenario: String, expectedHTTP: Int? = nil,
                       expectedError: URLError.Code? = nil, cancel: Bool = false) {
        let (client, session) = makeClient(scenario)
        defer { session.invalidateAndCancel() }
        let before = ControlState.serviceConnection()
        guard before.phase == .unknown else {
            XCTFail("Use a fresh isolated simulator with no saved connection state")
            return
        }
        let finished = expectation(description: scenario)
        let task = client.poll(credentials, after: "") { result, response, error in
            if let expectedHTTP, expectedHTTP == 200 {
                guard case .success(let page) = result else { XCTFail("expected page"); finished.fulfill(); return }
                XCTAssertEqual(page.sessionID, "fixture-session")
                XCTAssertEqual(page.nextCursor, "next")
                if scenario == "invalid-timing" || scenario == "float-timing" { XCTAssertNil(page.serverTimeMS) }
                else { XCTAssertEqual(page.serverTimeMS, 1000) }
                XCTAssertEqual(page.serverProcessingMS, 0)
                XCTAssertNotNil(page.requestStartNS)
                XCTAssertNotNil(page.responseEndNS)
                XCTAssertNil(error)
            } else if let expectedHTTP {
                guard case .failure(let failure) = result else { XCTFail("expected HTTP failure"); finished.fulfill(); return }
                XCTAssertEqual((failure as? LiveOperationsHTTPError)?.statusCode, expectedHTTP)
                XCTAssertNil(error)
            } else if let expectedError {
                guard case .failure(let failure) = result else { XCTFail("expected transport failure"); finished.fulfill(); return }
                XCTAssertEqual((failure as? URLError)?.code, expectedError)
                XCTAssertEqual((error as? URLError)?.code, expectedError)
            }
            XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, expectedHTTP)
            XCTAssertEqual(ControlState.serviceConnection(), before,
                           "poll decoding must not write shared connection diagnostics")
            finished.fulfill()
        }
        if cancel { task.cancel() }
        wait(for: [finished], timeout: 5)
        withExtendedLifetime(task) {}
    }

    func testSuccessfulPage() { check("success", expectedHTTP: 200) }
    func testMalformedTimingDoesNotFailPoll() { check("invalid-timing", expectedHTTP: 200) }
    func testFloatingTimingDoesNotFailPoll() { check("float-timing", expectedHTTP: 200) }
    func testMalformedPromptExpiryPreservesHistoryRecord() throws {
        let json = "{\"prompt_id\":\"p\",\"input_mode\":\"live_observation\",\"start_at_ms\":1,\"end_at_ms\":1,\"phenomenon\":\"x\",\"suggestion\":\"x\",\"display_text\":\"x\",\"observation_count\":1,\"expires_at_ms\":\"invalid\"}"
        let prompt = try JSONDecoder().decode(LivePrompt.self, from: Data(json.utf8))
        XCTAssertEqual(prompt.promptID, "p")
        XCTAssertNil(prompt.expiresAtMS)
    }
    func testMixedPageRetainsSharedAnchorAndValidExpiry() {
        let (client, session) = makeClient("mixed-timing")
        defer { session.invalidateAndCancel() }
        let finished = expectation(description: "mixed prompt timing")
        _ = client.poll(credentials, after: "") { result, _, _ in
            guard case .success(let page) = result else { XCTFail("expected mixed page"); finished.fulfill(); return }
            XCTAssertEqual(page.serverTimeMS, 1000)
            XCTAssertEqual(page.serverProcessingMS, 0)
            XCTAssertEqual(page.prompts.map(\.promptID), ["a", "bad", "b"])
            XCTAssertEqual(page.prompts.map(\.expiresAtMS), [5000, nil, 6000])
            finished.fulfill()
        }
        wait(for: [finished], timeout: 5)
    }
    func testHTTPFailure() { check("http-error", expectedHTTP: 503) }
    func testTransportFailure() { check("disconnect", expectedError: .cannotConnectToHost) }
    func testTimeout() { check("timeout", expectedError: .timedOut) }
    func testCancellation() { check("hold", expectedError: .cancelled, cancel: true) }

    func testOverviewDecodingDoesNotCommitConnectionDiagnostics() {
        for isSummary in [true, false] {
            let (client, session) = makeClient("http-error")
            let before = ControlState.serviceConnection()
            let finished = expectation(description: isSummary ? "summary" : "events")
            var observedStatus: Int?
            let onResponse: (URLResponse?, Error?) -> Void = { response, error in
                XCTAssertNil(error)
                observedStatus = (response as? HTTPURLResponse)?.statusCode
            }
            if isSummary {
                client.summary(credentials, onResponse: onResponse) { result in
                    guard case .failure(let error) = result else { XCTFail("expected HTTP failure"); finished.fulfill(); return }
                    XCTAssertEqual((error as? LiveOperationsHTTPError)?.statusCode, 503)
                    XCTAssertEqual(ControlState.serviceConnection(), before)
                    finished.fulfill()
                }
            } else {
                client.events(credentials, onResponse: onResponse) { result in
                    guard case .failure(let error) = result else { XCTFail("expected HTTP failure"); finished.fulfill(); return }
                    XCTAssertEqual((error as? LiveOperationsHTTPError)?.statusCode, 503)
                    XCTAssertEqual(ControlState.serviceConnection(), before)
                    finished.fulfill()
                }
            }
            wait(for: [finished], timeout: 5)
            XCTAssertEqual(observedStatus, 503)
            session.invalidateAndCancel()
        }
    }
}
