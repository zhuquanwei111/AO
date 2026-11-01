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
        if path.hasPrefix("/binding-a/") {
            guard request.url?.port == 8443,
                  request.value(forHTTPHeaderField: "X-Live-Session-Token") == "fixture-token" else {
                client?.urlProtocol(self, didFailWithError: URLError(.userAuthenticationRequired))
                return
            }
            let json = path.hasSuffix("/events")
                ? "{\"session_id\":\"fixture-session\",\"started_at_ms\":1,\"events\":[],\"next_cursor\":\"\",\"has_more\":false}"
                : "{\"session_id\":\"fixture-session\",\"started_at_ms\":1,\"online_count\":{\"status\":\"unknown\",\"coverage\":{}}}"
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: nil)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: Data(json.utf8))
            client?.urlProtocolDidFinishLoading(self)
            return
        }
        if path.hasSuffix("/stop"),
           (!path.contains("/sessions/fixture-session/stop") ||
            request.value(forHTTPHeaderField: "X-Live-Session-Token") != "fixture-token") {
            client?.urlProtocol(self, didFailWithError: URLError(.userAuthenticationRequired))
            return
        }
        if path.hasPrefix("/isolated-stop") {
            guard request.url?.host == "poll-fixture.invalid", request.url?.port == 8443,
                  path.hasSuffix("/v1/live/sessions/fixture-session/stop"),
                  request.value(forHTTPHeaderField: "X-Live-Session-Token") == "fixture-token" else {
                client?.urlProtocol(self, didFailWithError: URLError(.userAuthenticationRequired))
                return
            }
            if path.hasPrefix("/isolated-stop-timeout/") {
                client?.urlProtocol(self, didFailWithError: URLError(.timedOut))
                return
            }
        }
        if path.hasPrefix("/immediate-") {
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems
            guard query?.first(where: { $0.name == "wait_ms" })?.value == "0",
                  request.value(forHTTPHeaderField: "X-Live-Session-Token") == "fixture-token" else {
                client?.urlProtocol(self, didFailWithError: URLError(.badURL))
                return
            }
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
        let status = path.hasPrefix("/http-error/") ? 503
            : (path.hasPrefix("/immediate-unauthorized/") ? 401
                : (path.hasPrefix("/immediate-forbidden/") ? 403
                    : (path.hasPrefix("/immediate-missing/") ? 404 : 200)))
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
        let prompts: String
        if path.hasPrefix("/immediate-prompt/") {
            prompts = "[{\"prompt_id\":\"first\",\(promptFields),\"expires_at_ms\":5000,\"priority\":25}]"
        } else if path.hasPrefix("/mixed-metadata/") {
            prompts = "[{\"prompt_id\":\"a\",\(promptFields),\"expires_at_ms\":5000,\"priority\":100},{\"prompt_id\":\"bad\",\(promptFields),\"expires_at_ms\":5000,\"priority\":\"invalid\",\"sources\":false},{\"prompt_id\":\"broken\"},{\"prompt_id\":\"b\",\(promptFields),\"expires_at_ms\":6000,\"priority\":25,\"sources\":[{\"kind\":\"comment\"},false]},{\"prompt_id\":\"float\",\(promptFields),\"expires_at_ms\":6000,\"priority\":100.0},{\"prompt_id\":\"missing\",\(promptFields),\"expires_at_ms\":6000},{\"prompt_id\":\"null\",\(promptFields),\"expires_at_ms\":6000,\"priority\":null},{\"prompt_id\":\"bool\",\(promptFields),\"expires_at_ms\":6000,\"priority\":true}]"
        } else if path.hasPrefix("/mixed-timing/") {
            prompts = "[{\"prompt_id\":\"a\",\(promptFields),\"expires_at_ms\":5000},{\"prompt_id\":\"bad\",\(promptFields),\"expires_at_ms\":9.0},{\"prompt_id\":\"b\",\(promptFields),\"expires_at_ms\":6000}]"
        } else { prompts = "[]" }
        let stopped = path.hasPrefix("/immediate-stopped/") ? "true" : "false"
        let json = status == 200
            ? "{\"session_id\":\"fixture-session\",\"prompts\":\(prompts),\"stopped\":\(stopped),\"next_cursor\":\"next\",\(timing)}"
            : "{\"detail\":\"fixture\"}"
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(json.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

final class PollTransportTests: XCTestCase {
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

    func testCaptureBindingRejectsChangedOrMissingService() {
        var original = credentials
        let serviceA = URL(string: "https://poll-fixture.invalid:8443/original")!
        let serviceB = URL(string: "https://poll-fixture.invalid:8443/other")!
        XCTAssertNil(original.captureServiceURL(configured: serviceA))
        original.boundServiceURL = serviceA
        XCTAssertEqual(original.captureServiceURL(configured: serviceA), serviceA)
        var otherService = original
        otherService.boundServiceURL = serviceB
        XCTAssertTrue(original.hasSameBoundIdentity(as: original))
        XCTAssertFalse(original.hasSameBoundIdentity(as: otherService))
        XCTAssertFalse(original.hasSameBoundIdentity(as: nil))
        XCTAssertNil(original.captureServiceURL(configured: serviceB))
        XCTAssertNil(original.captureServiceURL(configured: nil))
        original.boundServiceURL = URL(string: "https://user:secret@poll-fixture.invalid")!
        XCTAssertNil(original.captureServiceURL(configured: original.boundServiceURL))
    }

    func testUnreadableStopRecordIsPreserved() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: url) }
        XCTAssertFalse(LiveSessionState.isolatedStopRecordUnreadable(fileURL: url))
        let invalid = Data("invalid stop record".utf8)
        try invalid.write(to: url)
        XCTAssertTrue(LiveSessionState.isolatedStopRecordUnreadable(fileURL: url))
        let intent = IsolatedStopIntent(credentials: credentials,
                                        serviceURL: URL(string: "https://poll-fixture.invalid/a")!)
        XCTAssertFalse(LiveSessionState.saveIsolatedStop(intent, fileURL: url))
        XCTAssertEqual(try Data(contentsOf: url), invalid)
    }

    func testOriginalServiceClientReadsEventsAndSummary() throws {
        var original = credentials
        original.boundServiceURL = URL(string: "https://poll-fixture.invalid:8443/binding-a")!
        let stored = try JSONEncoder().encode(original)
        let restored = try JSONDecoder().decode(LiveSessionCredentials.self, from: stored)
        let configuredB = URL(string: "https://poll-fixture.invalid:8443/binding-b")!
        XCTAssertNil(restored.captureServiceURL(configured: configuredB))
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [PollFixtureProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let client = try XCTUnwrap(restored.boundServiceURL.flatMap { LiveOperationsClient(baseURL: $0, session: session) })
        let events = expectation(description: "原服务事件")
        let summary = expectation(description: "原服务汇总")
        client.events(restored, onResponse: { _, _ in }) { result in
            guard case .success(let page) = result else { XCTFail("事件请求未成功"); events.fulfill(); return }
            XCTAssertEqual(page.sessionID, restored.sessionID)
            events.fulfill()
        }
        client.summary(restored, onResponse: { _, _ in }) { result in
            guard case .success(let page) = result else { XCTFail("汇总请求未成功"); summary.fulfill(); return }
            XCTAssertEqual(page.sessionID, restored.sessionID)
            summary.fulfill()
        }
        wait(for: [events, summary], timeout: 5)
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
    func testImmediatePollPreservesPromptAndStoppedState() {
        for scenario in ["immediate-prompt", "immediate-stopped"] {
            let (client, session) = makeClient(scenario)
            let finished = expectation(description: scenario)
            _ = client.poll(credentials, after: "", waitMS: 0) { result, _, _ in
                guard case .success(let page) = result else {
                    XCTFail("expected immediate page"); finished.fulfill(); return
                }
                XCTAssertEqual(page.sessionID, "fixture-session")
                XCTAssertEqual(page.stopped, scenario == "immediate-stopped")
                XCTAssertEqual(page.prompts.map(\.promptID), scenario == "immediate-prompt" ? ["first"] : [])
                XCTAssertEqual(page.nextCursor, "next")
                finished.fulfill()
            }
            wait(for: [finished], timeout: 5)
            session.invalidateAndCancel()
        }
    }
    func testImmediatePollRejectsUnauthorizedResponse() {
        for (scenario, expectedStatus) in [("immediate-unauthorized", 401), ("immediate-forbidden", 403), ("immediate-missing", 404)] {
            let (client, session) = makeClient(scenario)
            let before = ControlState.serviceConnection()
            let finished = expectation(description: scenario)
            _ = client.poll(credentials, after: "", waitMS: 0) { result, _, _ in
                guard case .failure(let error) = result else {
                    XCTFail("expected HTTP failure"); finished.fulfill(); return
                }
                XCTAssertEqual((error as? LiveOperationsHTTPError)?.statusCode, expectedStatus)
                XCTAssertEqual(ControlState.serviceConnection(), before)
                finished.fulfill()
            }
            wait(for: [finished], timeout: 5)
            session.invalidateAndCancel()
        }
    }
    func testPollCredentialsRejectReplacedToken() {
        XCTAssertTrue(PollCallbackGate.matchesCredentials(sessionID: "fixture-session", token: "old",
            currentSessionID: "fixture-session", currentToken: "old",
            savedSessionID: "fixture-session", savedToken: "old"))
        XCTAssertFalse(PollCallbackGate.matchesCredentials(sessionID: "fixture-session", token: "old",
            currentSessionID: "fixture-session", currentToken: "new",
            savedSessionID: "fixture-session", savedToken: "old"))
        XCTAssertFalse(PollCallbackGate.matchesCredentials(sessionID: "fixture-session", token: "old",
            currentSessionID: "fixture-session", currentToken: "old",
            savedSessionID: "fixture-session", savedToken: "new"))
    }
    func testPollDispositionSeparatesStaleAndSavedIdentityMismatch() {
        func check(currentToken: String?, savedToken: String?, generation: Int = 1,
                   active: Bool = true, stopping: Bool = false) -> PollCallbackGate.Disposition {
            PollCallbackGate.disposition(sessionID: "fixture-session", token: "fixture-token", generation: 1,
                currentSessionID: "fixture-session", currentToken: currentToken, currentGeneration: generation,
                savedSessionID: savedToken == nil ? nil : "fixture-session", savedToken: savedToken,
                active: active, stopping: stopping)
        }
        XCTAssertEqual(check(currentToken: "fixture-token", savedToken: "fixture-token"), .current)
        XCTAssertEqual(check(currentToken: "fixture-token", savedToken: nil), .savedCredentialsMismatch)
        XCTAssertEqual(check(currentToken: "fixture-token", savedToken: "other"), .savedCredentialsMismatch)
        XCTAssertEqual(check(currentToken: "other", savedToken: "fixture-token"), .stale)
        XCTAssertEqual(check(currentToken: "fixture-token", savedToken: "fixture-token", generation: 2), .stale)
        XCTAssertEqual(check(currentToken: "fixture-token", savedToken: "fixture-token", stopping: true), .stale)
    }
    func testIsolatedStopUsesFrozenServiceAndKeepsSharedState() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let slot = directory.appendingPathComponent("pending.json")
        let otherSession = directory.appendingPathComponent("current.json")
        let otherBytes = Data("{\"session_id\":\"other\",\"session_token\":\"other-token\",\"started_at_ms\":1}".utf8)
        try otherBytes.write(to: otherSession)
        for (path, expected) in [("isolated-stop", true), ("isolated-stop-timeout", false)] {
            let url = try XCTUnwrap(URL(string: "https://poll-fixture.invalid:8443/\(path)"))
            let intent = IsolatedStopIntent(credentials: credentials, serviceURL: url)
            XCTAssertTrue(LiveSessionState.saveIsolatedStop(intent, fileURL: slot))
            let restored = try XCTUnwrap(LiveSessionState.isolatedStopIntent(fileURL: slot))
            XCTAssertEqual(restored.serviceURL, url)
            let configuration = URLSessionConfiguration.ephemeral
            configuration.protocolClasses = [PollFixtureProtocol.self]
            let session = URLSession(configuration: configuration)
            let client = try XCTUnwrap(LiveOperationsClient(baseURL: restored.serviceURL, session: session))
            let beforeConnection = ControlState.serviceConnection()
            let beforeCapture = ControlState.userWantsCapture()
            let beforeEnabled = LiveSessionState.isEnabled()
            let finished = expectation(description: path)
            client.stop(restored.credentials, recordResponse: false) { ok, _, _ in
                XCTAssertEqual(ok, expected)
                XCTAssertEqual(ControlState.serviceConnection(), beforeConnection)
                XCTAssertEqual(ControlState.userWantsCapture(), beforeCapture)
                XCTAssertEqual(LiveSessionState.isEnabled(), beforeEnabled)
                finished.fulfill()
            }
            wait(for: [finished], timeout: 5)
            session.invalidateAndCancel()
            XCTAssertEqual(try Data(contentsOf: otherSession), otherBytes)
            if expected { XCTAssertTrue(LiveSessionState.clearIsolatedStop(restored, fileURL: slot)) }
            else { XCTAssertTrue(LiveSessionState.isolatedStopIntent(fileURL: slot)?.matches(restored) == true) }
        }
        let unknown = directory.appendingPathComponent("unknown.json")
        try Data("{\"credentials\":{\"session_id\":\"fixture-session\",\"session_token\":\"fixture-token\",\"started_at_ms\":1}}".utf8).write(to: unknown)
        XCTAssertNil(LiveSessionState.isolatedStopIntent(fileURL: unknown))
        let safe = IsolatedStopIntent(credentials: credentials, serviceURL: try XCTUnwrap(URL(string: "https://poll-fixture.invalid:8443/isolated-stop")))
        XCTAssertFalse(LiveSessionState.saveIsolatedStop(safe, fileURL: unknown))
        XCTAssertFalse(LiveSessionState.saveIsolatedStop(safe, fileURL: directory.appendingPathComponent("missing/pending.json")))
        let otherService = IsolatedStopIntent(credentials: credentials, serviceURL: try XCTUnwrap(URL(string: "https://poll-fixture.invalid:9443/isolated-stop")))
        XCTAssertFalse(LiveSessionState.saveIsolatedStop(otherService, fileURL: slot))
        XCTAssertFalse(LiveSessionState.clearIsolatedStop(otherService, fileURL: slot))
        for text in ["https://u:p@poll-fixture.invalid/", "https://poll-fixture.invalid/?secret=x", "https://poll-fixture.invalid/#fragment"] {
            let invalid = IsolatedStopIntent(credentials: credentials, serviceURL: try XCTUnwrap(URL(string: text)))
            XCTAssertFalse(LiveSessionState.saveIsolatedStop(invalid, fileURL: directory.appendingPathComponent(UUID().uuidString)))
        }
    }
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
    func testMixedMetadataDoesNotBlockValidPageOrCursor() {
        let (client, session) = makeClient("mixed-metadata")
        defer { session.invalidateAndCancel() }
        let finished = expectation(description: "mixed prompt metadata")
        _ = client.poll(credentials, after: "") { result, _, _ in
            guard case .success(let page) = result else { XCTFail("expected mixed page"); finished.fulfill(); return }
            XCTAssertEqual(page.nextCursor, "next")
            XCTAssertEqual(page.prompts.map(\.promptID), ["a", "bad", "b", "float", "missing", "null", "bool"])
            XCTAssertEqual(page.prompts.map(\.priority), [100, nil, 25, nil, nil, nil, nil])
            XCTAssertEqual(page.invalidEntryCount, 1)
            XCTAssertEqual(page.rawPromptIndices, [0, 1, 3, 4, 5, 6, 7])
            XCTAssertTrue(page.prompts[1].priorityInvalid)
            XCTAssertTrue(page.prompts[1].sourcesInvalid)
            XCTAssertFalse(page.prompts[0].priorityInvalid)
            XCTAssertTrue(page.prompts[2].sourcesInvalid)
            XCTAssertEqual(page.prompts[2].sources?.first?.kind, "comment")
            XCTAssertTrue(page.prompts[3].priorityInvalid)
            XCTAssertTrue(page.prompts[4...6].allSatisfy(\.priorityInvalid))
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
