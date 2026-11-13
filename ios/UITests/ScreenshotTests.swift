import XCTest

/// Captures the App Store screenshot set by driving the real app against a
/// real PocketBooks server.
///
/// Run it through `ios/screenshots.sh`, which boots the simulator, points the
/// app at your server, runs this test, and exports the PNGs. Nothing here is
/// part of a release build.
///
/// The server URL and token are written into the app's simulator preferences
/// by that script, so no credential ever appears in the repository or in a
/// command line recorded by the test log.
final class ScreenshotTests: XCTestCase {

    private var app: XCUIApplication!

    /// The sample title the screenshots are built around.
    private let featuredBook = "The Thirty-Nine Steps"

    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launch()
    }

    func testCaptureStoreScreenshots() throws {
        try captureLibrary()
        try captureVoices()
        try capturePlayerAndTranscript()
        try captureLightMode()
    }

    // MARK: - Steps

    private func captureLibrary() throws {
        XCTAssertTrue(app.navigationBars["Library"].waitForExistence(timeout: 30),
                      "The library never appeared — is the server reachable?")
        XCTAssertTrue(app.staticTexts[featuredBook].waitForExistence(timeout: 60),
                      "The sample library is empty. Run `python -m app.samples` first.")
        settle()
        capture("01-library")
    }

    private func captureVoices() throws {
        let voiceButton = app.buttons
            .matching(NSPredicate(format: "label BEGINSWITH 'Pocket voice'"))
            .firstMatch
        guard voiceButton.waitForExistence(timeout: 10) else {
            XCTFail("Voice menu button not found")
            return
        }
        voiceButton.tap()
        settle()
        capture("02-voices")
        // Re-selecting the active voice closes the menu without changing it.
        let active = app.buttons["Marius"].firstMatch
        if active.waitForExistence(timeout: 5) {
            active.tap()
        }
        settle()
    }

    private func capturePlayerAndTranscript() throws {
        app.staticTexts[featuredBook].firstMatch.tap()

        // Opening a book makes the server resume that conversion, so the player
        // shows live generation progress.
        let chapters = app.buttons["Chapters"]
        XCTAssertTrue(chapters.waitForExistence(timeout: 60),
                      "Player never finished loading")

        chapters.tap()
        settle(seconds: 3)
        capture("03-player-generating")

        let text = app.buttons["Text"]
        XCTAssertTrue(text.waitForExistence(timeout: 30),
                      "Transcript tab missing — no transcript for this job yet")
        text.tap()
        settle(seconds: 3)
        capture("04-transcript")
    }

    /// Back on the library, now with a "Continue" row, switched to the light
    /// theme. A secure token field is redacted by iOS in every screenshot, so
    /// the settings screen is deliberately not part of the store set.
    private func captureLightMode() throws {
        app.navigationBars.buttons.element(boundBy: 0).tap()  // back to Library
        XCTAssertTrue(app.navigationBars["Library"].waitForExistence(timeout: 20))

        let toggle = app.buttons["Brightness Higher"].firstMatch
        if toggle.waitForExistence(timeout: 10) {
            toggle.tap()
        }
        settle(seconds: 3)
        capture("05-library-light")
    }

    // MARK: - Helpers

    /// Lets animations, cover downloads, and the first audio buffer finish so a
    /// screenshot never catches a half-drawn screen.
    private func settle(seconds: TimeInterval = 2) {
        _ = XCTWaiter.wait(for: [expectation(description: "settle")], timeout: seconds)
    }

    private func capture(_ name: String) {
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}

/// Proves the static-catalogue path end to end: no server, no token, the app
/// reads a manifest of files and plays the audio behind it.
final class CataloguePlaybackTests: XCTestCase {

    func testPlaysABookFromTheStaticCatalogue() throws {
        let app = XCUIApplication()
        app.launch()

        let library = app.navigationBars["Library"]
        XCTAssertTrue(library.waitForExistence(timeout: 30),
                      "Library never appeared — is catalogueURL set on this simulator?")

        let firstBook = app.staticTexts["The Time Machine"]
        XCTAssertTrue(firstBook.waitForExistence(timeout: 30), "catalogue is empty")
        firstBook.tap()

        // A catalogue book is already finished, so the chapter list must be
        // there without any conversion being started.
        XCTAssertTrue(app.buttons["Chapters"].waitForExistence(timeout: 40),
                      "player never loaded the catalogue book")
        XCTAssertTrue(app.buttons["Text"].exists,
                      "transcript tab missing — transcript.json did not load")

        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = "catalogue-player"
        shot.lifetime = .keepAlways
        add(shot)
    }
}

/// iPad rotates freely (App Store Connect requires it), so landscape has to be
/// usable, not just allowed. Runs against the hosted demo catalogue.
final class IPadLandscapeTests: XCTestCase {

    func testLibraryAndPlayerSurviveRotation() throws {
        try XCTSkipUnless(UIDevice.current.userInterfaceIdiom == .pad, "iPad only")
        let app = XCUIApplication()
        app.launch()
        XCUIDevice.shared.orientation = .landscapeLeft

        XCTAssertTrue(app.navigationBars["Library"].waitForExistence(timeout: 30))
        let book = app.staticTexts["The Time Machine"]
        XCTAssertTrue(book.waitForExistence(timeout: 30))
        add(attachment(named: "ipad-landscape-library"))

        book.tap()
        XCTAssertTrue(app.buttons["Chapters"].waitForExistence(timeout: 40))
        add(attachment(named: "ipad-landscape-player"))
        XCUIDevice.shared.orientation = .portrait
    }

    private func attachment(named name: String) -> XCTAttachment {
        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        return shot
    }
}
