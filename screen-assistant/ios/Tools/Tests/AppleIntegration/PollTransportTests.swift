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
        let json = status == 200
            ? "{\"session_id\":\"fixture-session\",\"prompts\":[],\"stopped\":false,\"next_cursor\":\"next\"}"
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
    func testHTTPFailure() { check("http-error", expectedHTTP: 503) }
    func testTransportFailure() { check("disconnect", expectedError: .cannotConnectToHost) }
    func testTimeout() { check("timeout", expectedError: .timedOut) }
    func testCancellation() { check("hold", expectedError: .cancelled, cancel: true) }
}
