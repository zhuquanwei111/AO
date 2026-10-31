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
        let prompts: String
        if path.hasPrefix("/mixed-metadata/") {
            prompts = "[{\"prompt_id\":\"a\",\(promptFields),\"expires_at_ms\":5000,\"priority\":100},{\"prompt_id\":\"bad\",\(promptFields),\"expires_at_ms\":5000,\"priority\":\"invalid\",\"sources\":false},{\"prompt_id\":\"broken\"},{\"prompt_id\":\"b\",\(promptFields),\"expires_at_ms\":6000,\"priority\":25,\"sources\":[{\"kind\":\"comment\"},false]},{\"prompt_id\":\"float\",\(promptFields),\"expires_at_ms\":6000,\"priority\":100.0},{\"prompt_id\":\"missing\",\(promptFields),\"expires_at_ms\":6000},{\"prompt_id\":\"null\",\(promptFields),\"expires_at_ms\":6000,\"priority\":null},{\"prompt_id\":\"bool\",\(promptFields),\"expires_at_ms\":6000,\"priority\":true}]"
        } else if path.hasPrefix("/mixed-timing/") {
            prompts = "[{\"prompt_id\":\"a\",\(promptFields),\"expires_at_ms\":5000},{\"prompt_id\":\"bad\",\(promptFields),\"expires_at_ms\":9.0},{\"prompt_id\":\"b\",\(promptFields),\"expires_at_ms\":6000}]"
        } else { prompts = "[]" }
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
