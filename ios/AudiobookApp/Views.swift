import AVFoundation
import Combine
import SwiftUI

@main
struct PocketBooksApp: App {
    @StateObject private var settings = AppSettings()
    var body: some Scene {
        WindowGroup {
            Group {
                if settings.isConfigured {
                    RootTabView()
                } else {
                    NavigationStack { SettingsView() }
                }
            }
            .environmentObject(settings)
            .preferredColorScheme(appColorScheme(settings.appearance))
        }
    }
}

/// Map the stored appearance setting to a SwiftUI color scheme (nil = system).
func appColorScheme(_ s: String) -> ColorScheme? {
    switch s {
    case "light": return .light
    case "dark":  return .dark
    default:      return nil
    }
}

/// Light/Dark toggle shown top-right on every page.
struct ThemeToggleButton: View {
    @EnvironmentObject var settings: AppSettings
    var body: some View {
        Button {
            settings.appearance = (settings.appearance == "dark") ? "light" : "dark"
        } label: {
            Image(systemName: settings.appearance == "dark" ? "sun.max.fill" : "moon.fill")
        }
    }
}

/// Single tab: the folder library with integrated "Continue Listening" strip
/// and offline filter — Audible-style: one coherent browsing surface instead
/// of three separate tabs.
struct RootTabView: View {
    var body: some View {
        TabView {
            LibraryView()
                .tabItem { Label("Library", systemImage: "books.vertical") }
        }
    }
}

/// Edits live in local @State and are only committed to AppSettings when the
/// user taps Connect (after a reachability/auth check). This avoids the trap
/// where typing one token character flips `isConfigured` and navigates away
/// mid-input, firing a request with an incomplete token.
struct SettingsView: View {
    @EnvironmentObject var settings: AppSettings
    @Environment(\.dismiss) private var dismiss
    @State private var baseURL = ""
    @State private var token = ""
    @State private var checking = false
    @State private var loadingCatalogue = false
    @State private var error: String?

    /// Hosted catalogue of pre-rendered public-domain books.
    static let demoCatalogueURL = "https://www.mauli.app/pocketbooks/catalogue/"

    var body: some View {
        Form {
            Section("Server") {
                TextField("http://192.168.1.10:8725", text: $baseURL)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .keyboardType(.URL)
                SecureField("API token", text: $token)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
            }
            if let error { Text(error).foregroundStyle(.red).font(.footnote) }
            Section {
                Button {
                    Task { await connect() }
                } label: {
                    HStack {
                        Text(checking ? "Connecting…" : "Connect")
                        if checking { Spacer(); ProgressView() }
                    }
                }
                .disabled(baseURL.isEmpty || token.isEmpty || checking)
            } footer: {
                Text("Same WiFi: use the Mac's LAN IP. From anywhere: the Tailscale name. The token is printed by the server on startup.")
            }

            Section {
                if settings.isCatalogue {
                    Button(role: .destructive) {
                        settings.catalogueURL = ""
                    } label: { Text("Leave the demo library") }
                } else {
                    Button {
                        Task { await useCatalogue() }
                    } label: {
                        HStack {
                            Text(loadingCatalogue ? "Loading…" : "Try the demo library")
                            if loadingCatalogue { Spacer(); ProgressView() }
                        }
                    }
                    .disabled(loadingCatalogue)
                }
            } header: {
                Text("No server yet?")
            } footer: {
                Text(settings.isCatalogue
                     ? "Pre-generated public-domain books, served as plain files. Your listening position stays on this device."
                     : "Five public-domain books, already generated. No Mac, no server, no token \u{2014} just listen.")
            }
        }
        .navigationTitle("Settings")
        .onAppear { baseURL = settings.baseURL; token = settings.token }
    }

    /// Switch to the hosted demo catalogue, but only after proving it loads —
    /// a broken address must not leave the app stuck on an empty library.
    private func useCatalogue() async {
        loadingCatalogue = true; error = nil
        defer { loadingCatalogue = false }
        do {
            _ = try await CatalogueClient.shared.load(from: Self.demoCatalogueURL)
            settings.catalogueURL = Self.demoCatalogueURL
            dismiss()
        } catch {
            self.error = (error as? LocalizedError)?.errorDescription
                ?? "The demo library is not reachable right now."
        }
    }

    private func connect() async {
        checking = true; error = nil
        defer { checking = false }
        let url = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let tok = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let probe = URL(string: url + "/api/library") else {
            error = "Invalid server URL"; return
        }
        var req = URLRequest(url: probe, timeoutInterval: 8)
        req.setValue("Bearer \(tok)", forHTTPHeaderField: "Authorization")
        do {
            let (_, resp) = try await URLSession.shared.data(for: req)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if code == 401 { error = "Token rejected (401). Check the API token."; return }
            if !(200...299).contains(code) { error = "Server error (\(code))."; return }
            // Only now commit — this flips isConfigured and shows the library.
            settings.baseURL = url
            settings.token = tok
            dismiss()
        } catch {
            self.error = "Can't reach the server. Check the URL and that the Mac is on."
        }
    }
}

/// Root: a folder browser starting at the library root.
struct LibraryView: View {
    @EnvironmentObject var settings: AppSettings
    var body: some View {
        NavigationStack { FolderView(folder: "", title: "Library") }
    }
}

/// One folder level: its subfolders (with recursive book counts) and the books
/// directly inside it. The search field queries ALL books globally, regardless
/// of the current folder.
///
/// On the root level only: a "Continue Listening" horizontal strip (Audible-style)
/// and an All/Downloaded filter toggle.
struct FolderView: View {
    @EnvironmentObject var settings: AppSettings
    @Environment(\.scenePhase) private var scenePhase
    @ObservedObject private var downloads = DownloadManager.shared
    let folder: String
    let title: String

    @State private var subfolders: [LibraryFolder] = []
    @State private var books: [Book] = []
    @State private var startedBooks: [Book] = []
    @State private var query = ""
    @State private var error: String?
    @State private var loading = false
    @State private var showDownloadedOnly = false
    @AppStorage("voiceID") private var voiceID = 0

    private var api: APIClient { APIClient(settings: settings) }
    private var searching: Bool { !query.trimmingCharacters(in: .whitespaces).isEmpty }
    private var isRoot: Bool { folder.isEmpty }

    var body: some View {
        List {
            // Continue Listening strip — only on root, not searching, not filtered
            if isRoot, !searching, !showDownloadedOnly, !startedBooks.isEmpty {
                Section("Continue") {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 12) {
                            ForEach(startedBooks) { book in
                                NavigationLink(value: book) {
                                    VStack(alignment: .leading, spacing: 4) {
                                        AuthImage(url: api.coverURL(book.bookId),
                                                  token: settings.token) {
                                            RoundedRectangle(cornerRadius: 4).fill(Color.gray.opacity(0.2))
                                        }
                                        .scaledToFill()
                                        .frame(width: 72, height: 100).clipped()
                                        .clipShape(RoundedRectangle(cornerRadius: 4))
                                        Text(book.title)
                                            .font(.caption).lineLimit(1)
                                            .frame(width: 72)
                                        if let frac = book.fractionComplete {
                                            ProgressView(value: frac)
                                                .tint(.blue)
                                                .frame(width: 72, height: 3)
                                        }
                                    }
                                    .frame(width: 72)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(.horizontal)
                    }
                    .listRowInsets(EdgeInsets())
                }
            }
            if !searching, !showDownloadedOnly, !subfolders.isEmpty {
                Section("Folders") {
                    ForEach(subfolders) { f in
                        NavigationLink(value: f) {
                            Label {
                                HStack {
                                    Text(f.name)
                                    Spacer()
                                    Text("\(f.count)").foregroundStyle(.secondary)
                                }
                            } icon: { Image(systemName: "folder.fill").foregroundStyle(.tint) }
                        }
                    }
                }
            }
            if !books.isEmpty {
                Section(showDownloadedOnly ? "Downloaded" : (searching ? "Results" : "Books")) {
                    ForEach(books) { book in
                        BookRow(book: book, showFolder: searching || showDownloadedOnly)
                            .environmentObject(settings)
                    }
                }
            }
            if !loading, subfolders.isEmpty, books.isEmpty {
                Text(showDownloadedOnly ? "No downloads yet"
                     : searching ? "No matches"
                     : "Empty folder")
                    .foregroundStyle(.secondary)
            }
        }
        .navigationTitle(title)
        .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always),
                    prompt: "Search all books")
        .navigationDestination(for: LibraryFolder.self) { f in
            FolderView(folder: f.path, title: f.name).environmentObject(settings)
        }
        .navigationDestination(for: Book.self) { book in
            PlayerView(book: book).environmentObject(settings)
        }
        .toolbar {
            if isRoot {
                ToolbarItem(placement: .navigationBarLeading) {
                    Picker("Filter", selection: $showDownloadedOnly) {
                        Text("All").tag(false)
                        Text("Downloaded").tag(true)
                    }
                    .pickerStyle(.segmented)
                }
            }
            ToolbarItem(placement: .navigationBarTrailing) { ThemeToggleButton() }
            if isRoot, !settings.isCatalogue {
                ToolbarItem(placement: .navigationBarTrailing) { VoiceMenu() }
                ToolbarItem(placement: .navigationBarTrailing) {
                    NavigationLink { SettingsView() } label: { Image(systemName: "gearshape") }
                }
            }
        }
        .refreshable { await load() }
        .task(id: "\(query)|\(voiceID)|\(showDownloadedOnly)|\(settings.catalogueURL)|\(settings.isCatalogue && scenePhase == .active)") {
            if settings.isCatalogue && scenePhase != .active { return }
            await load()
        }
        .overlay { if let e = error { Text(e).foregroundStyle(.red).padding() } }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        let q = query.trimmingCharacters(in: .whitespaces)
        if !q.isEmpty {
            try? await Task.sleep(nanoseconds: 250_000_000)
            if Task.isCancelled { return }
        }

        // Offline-only: show downloaded books
        if showDownloadedOnly {
            var offline = downloads.offlineBooks()
            if !q.isEmpty {
                let lq = q.lowercased()
                offline = offline.filter {
                    $0.title.lowercased().contains(lq) ||
                    $0.author.lowercased().contains(lq)
                }
            }
            subfolders = []
            books = offline
            error = nil
            return
        }

        // Static catalogue: one manifest, filtered on the device.
        if settings.isCatalogue {
            do {
                var all = try await CatalogueClient.shared.load(from: settings.catalogueURL)
                guard !Task.isCancelled else { return }
                if !q.isEmpty {
                    let lq = q.lowercased()
                    all = all.filter { $0.title.lowercased().contains(lq)
                                    || $0.author.lowercased().contains(lq) }
                }
                subfolders = []
                books = all
                startedBooks = all.filter { ($0.fractionComplete ?? 0) > 0.01 }
                settings.features = ServerFeatures(recap: false)
                error = nil
            } catch {
                guard !Task.isCancelled else { return }
                self.error = (error as? LocalizedError)?.errorDescription
                    ?? "Cannot reach the catalogue"
            }
            return
        }

        // Online: load from server
        do {
            let r = try await api.browse(folder: folder, q: q.isEmpty ? nil : q,
                                         voiceID: voiceID == 0 ? nil : voiceID)
            subfolders = r.subfolders
            books = r.books
            // Older servers omit this and support everything.
            settings.features = r.features ?? ServerFeatures()
            if UserDefaults.standard.object(forKey: "voiceID") == nil,
               let first = r.voices.first {
                UserDefaults.standard.set(first.id, forKey: "voiceID")
            }
            // Load started books for the Continue strip (root only, in background)
            if isRoot {
                Task {
                    if let sb = try? await api.started(voiceID: voiceID == 0 ? nil : voiceID) {
                        startedBooks = sb
                    }
                }
            }
            error = nil
        } catch {
            // Offline fallback: if server unreachable, show downloads automatically
            let offline = downloads.offlineBooks()
            if !offline.isEmpty && isRoot {
                subfolders = []
                books = offline
                self.error = nil
            } else {
                self.error = "Cannot reach server"
            }
        }
    }
}

/// Selects one of the built-in Pocket TTS voices exposed by the server. Voice
/// IDs are deliberately loaded from the API rather than assumed locally.
struct VoiceMenu: View {
    @EnvironmentObject private var settings: AppSettings
    @AppStorage("voiceID") private var voiceID = 0
    @State private var voices: [Voice] = []

    private let supportedNames = ["Marius", "Javert", "Peter", "Bill", "Caro", "Alba"]
    private var api: APIClient { APIClient(settings: settings) }
    private var selectedName: String {
        voices.first(where: { $0.id == voiceID })?.name ?? "Voice"
    }

    var body: some View {
        Menu {
            Picker("Pocket voice", selection: $voiceID) {
                ForEach(voices) { voice in
                    Text(voice.name).tag(voice.id)
                }
            }
        } label: {
            Label(selectedName, systemImage: "waveform")
        }
        .accessibilityLabel("Pocket voice: \(selectedName)")
        .task { await loadVoices() }
    }

    private func loadVoices() async {
        guard let response = try? await api.browse(voiceID: nil) else { return }
        let byName = Dictionary(uniqueKeysWithValues: response.voices.map { ($0.name, $0) })
        voices = supportedNames.compactMap { byName[$0] }
        if voiceID == 0 || !voices.contains(where: { $0.id == voiceID }),
           let preferred = voices.first(where: { $0.name == "Marius" }) ?? voices.first {
            voiceID = preferred.id
        }
    }
}

/// Shared row used by the library: cover, title, progress bar. Tapping opens
/// the player — no download button, no status badge. Pure navigation.
/// Offline books get a subtle green checkmark.
struct BookRow: View {
    @EnvironmentObject var settings: AppSettings
    @ObservedObject private var downloads = DownloadManager.shared
    let book: Book
    var showFolder = false

    private var api: APIClient { APIClient(settings: settings) }

    var body: some View {
        NavigationLink(value: book) {
            HStack(spacing: 12) {
                AuthImage(url: api.coverURL(book.bookId),
                          token: settings.token) { Color.gray.opacity(0.2) }
                    .scaledToFill()
                    .frame(width: 44, height: 60).clipped()
                    .clipShape(RoundedRectangle(cornerRadius: 4))
                VStack(alignment: .leading, spacing: 3) {
                    Text(book.title).font(.headline).lineLimit(2)
                    Text(book.author).font(.subheadline).foregroundStyle(.secondary)
                        .lineLimit(1)
                    HStack(spacing: 4) {
                        if let frac = book.fractionComplete {
                            ProgressView(value: frac)
                                .tint(.blue)
                                .frame(height: 4)
                                .frame(maxWidth: 60)
                        }
                        if showFolder, !book.folder.isEmpty {
                            Text(book.folder).font(.caption2)
                                .foregroundStyle(.secondary).lineLimit(1)
                        }
                        if downloads.isDownloaded(book.bookId) {
                            Image(systemName: "arrow.down.circle.fill")
                                .font(.caption2).foregroundStyle(.green)
                        }
                    }
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

struct PlayerView: View {
    @EnvironmentObject var settings: AppSettings
    @ObservedObject private var downloads = DownloadManager.shared
    @Environment(\.colorScheme) private var scheme
    let book: Book
    @StateObject private var vm: PlayerViewModelHolder = .init()
    @State private var tab = 0   // 0 = chapters, 1 = running text
    @State private var showRecap = false

    private var api: APIClient { APIClient(settings: settings) }

    var body: some View {
        VStack(spacing: 12) {
            // Now-playing bar: cover thumbnail + title/author (compact, so the
            // text/chapter area below stays large).
            HStack(spacing: 12) {
                AuthImage(url: api.coverURL(book.bookId),
                          token: settings.token) {
                    RoundedRectangle(cornerRadius: 6).fill(Color.secondary.opacity(0.2))
                }
                .scaledToFill()
                .frame(width: 58, height: 82).clipped()
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6)
                    .strokeBorder(Color.primary.opacity(0.12)))
                VStack(alignment: .leading, spacing: 3) {
                    Text(book.title).font(.headline).lineLimit(2)
                    Text(book.author).font(.subheadline)
                        .foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer(minLength: 0)
            }

            if let m = vm.model {
                ScrubberView(vm: m)
                // Error
                if let err = vm.model?.lastError {
                    Label(err, systemImage: "exclamationmark.triangle")
                        .font(.caption).foregroundStyle(.red)
                }
                // Status + download button — every state tells the user what's happening.
                HStack(spacing: 16) {
                    playerStatusLabel(m: m)
                    Spacer()
                    // Download / delete button — three distinct visual states.
                    // A static catalogue publishes no M4B, so there is nothing
                    // to download and the control would only mislead.
                    if settings.isCatalogue {
                        EmptyView()
                    } else if downloads.isDownloaded(book.bookId) {
                        Button(role: .destructive) {
                            downloads.delete(book.bookId)
                        } label: {
                            Image(systemName: "checkmark.circle.fill")
                                .font(.title3).foregroundStyle(.green)
                                .accessibilityLabel("Downloaded — tap to delete")
                        }
                    } else if let pct = downloads.progress[book.bookId], pct > 0, pct < 1 {
                        // Actively downloading: show the percentage ring
                        ProgressView(value: pct)
                            .progressViewStyle(.circular)
                            .frame(width: 26, height: 26)
                            .accessibilityLabel("\(Int(pct * 100)) percent downloaded")
                    } else if downloads.progress[book.bookId] != nil {
                        // Just finished — show the green check briefly before the
                        // manifest reload swaps in the green-checkmark button.
                        ProgressView()
                            .frame(width: 24, height: 24)
                    } else {
                        Button {
                            vm.model?.downloadForOffline()
                        } label: {
                            Image(systemName: "arrow.down.circle")
                                .font(.title3)
                        }
                        .disabled(vm.model?.jobID == nil || !(vm.model?.status?.hasM4b ?? false))
                        .accessibilityLabel("Download for offline — enabled once audio is ready")
                    }
                }
            }

            HStack(spacing: 40) {
                Button { vm.model?.seek(to: (vm.model?.currentTime ?? 0) - 15) } label:
                    { Image(systemName: "gobackward.15") }
                Button { vm.model?.isPlaying == true ? vm.model?.pause() : vm.model?.play() } label:
                    { Image(systemName: vm.model?.isPlaying == true ? "pause.circle.fill" : "play.circle.fill")
                        .resizable().frame(width: 58, height: 58) }
                Button { vm.model?.seek(to: (vm.model?.currentTime ?? 0) + 30) } label:
                    { Image(systemName: "goforward.30") }
            }.font(.title)

            HStack(spacing: 28) {
                sleepTimerControl
            }

            let hasText = !(vm.model?.transcript.isEmpty ?? true)
            if hasText {
                Picker("", selection: $tab) {
                    Text("Chapters").tag(0)
                    Text("Text").tag(1)
                }.pickerStyle(.segmented)
                // Switch to text tab the moment a transcript arrives (offline or online).
                .onChange(of: vm.model?.transcript.count ?? 0) { newCount in
                    if newCount > 0 { tab = 1 }
                }
            }

            if tab == 1 && hasText, let m = vm.model {
                LyricsView(vm: m)
            } else if let m = vm.model, !m.chapters.isEmpty {
                chapterList(m)
            }
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .tint(tint)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                if settings.features.recap {
                    Button {
                        vm.model?.pause()
                        showRecap = true
                    } label: { Image(systemName: "text.bubble") }
                    .accessibilityLabel("Previously on…")
                }
            }
            ToolbarItem(placement: .navigationBarTrailing) { ThemeToggleButton() }
        }
        .sheet(isPresented: $showRecap) {
            RecapSheet(api: api, bookID: book.bookId,
                       positionS: vm.model?.currentTime ?? 0)
        }
        .onAppear { vm.configure(api: api, book: book) }
        .onDisappear { vm.model?.stop() }
    }

    /// Accent that keeps contrast in both themes: a deep blue on the light wash
    /// (the system light-blue was too pale), the bright blue in dark mode.
    private var tint: Color {
        scheme == .dark ? .blue : Color(red: 0.04, green: 0.28, blue: 0.62)
    }

    /// Tells the user what the player is currently doing. Six explicit states —
/// no more guessing "is the thing playing, loading, broken, or downloading?".
@ViewBuilder private func playerStatusLabel(m: PlayerViewModel) -> some View {
    // 1. Offline & verified playable
    if m.offline {
        Label("Offline", systemImage: "arrow.down.circle.fill")
            .font(.caption2).foregroundStyle(.green)
    }
    // 2. Player attached but still buffering (HLS first segment)
    else if m.isAttached, !m.isPlaying, m.currentTime < 1,
            let st = m.status, !st.playable {
        Label("Buffering…", systemImage: "antenna.radiowaves.left.and.right")
            .font(.caption2).foregroundStyle(.secondary)
    }
    // 3. Server is actively generating audio chunks
    else if let st = m.status, st.state == "processing" {
        Label("Generating \(st.doneChunks)/\(st.totalChunks)",
              systemImage: "hourglass")
            .font(.caption2).foregroundStyle(.secondary)
    }
    // 4. Waiting for first chunk (server hasn't started, or no playable stream yet)
    else if !m.isAttached, let st = m.status, st.state == "queued" {
        Label("Queued on server", systemImage: "hourglass")
            .font(.caption2).foregroundStyle(.secondary)
    }
    // 5. Job done but HLS stream isn't playable yet (rare)
    else if !m.isAttached, let st = m.status, st.state == "done", !st.playable {
        Label("Server audio ready, loading…",
              systemImage: "arrow.triangle.2.circlepath")
            .font(.caption2).foregroundStyle(.secondary)
    }
    // 6. Player attached and either playing or paused mid-track — no label needed
}

    @ViewBuilder private func chapterList(_ m: PlayerViewModel) -> some View {
        let current = m.currentChapterIndex()
        List(m.chapters) { ch in
            let isCurrent = ch.index == current
            Button { m.seek(toChapter: ch) } label: {
                HStack {
                    if isCurrent {
                        Image(systemName: "speaker.wave.2.fill")
                            .font(.caption).foregroundStyle(.tint)
                    }
                    Text(ch.title)
                        .fontWeight(isCurrent ? .semibold : .regular)
                        .foregroundStyle(ch.startS == nil ? .secondary
                                         : (isCurrent ? tint : .primary))
                    Spacer()
                    if ch.startS == nil {
                        Image(systemName: "hourglass").foregroundStyle(.secondary)
                    }
                }
            }
            .disabled(ch.startS == nil)
            .listRowBackground(isCurrent ? Color.primary.opacity(0.06) : nil)
        }
    }

    /// Sleep timer: 30 / 45 / 60 minutes, with the remaining time shown and a
    /// way to turn it off. Pauses playback when it elapses.
    @ViewBuilder private var sleepTimerControl: some View {
        let remaining = vm.model?.sleepRemaining
        Menu {
            Button("30 minutes") { vm.model?.startSleepTimer(minutes: 30) }
            Button("45 minutes") { vm.model?.startSleepTimer(minutes: 45) }
            Button("60 minutes") { vm.model?.startSleepTimer(minutes: 60) }
            if remaining != nil {
                Button("Turn off", role: .destructive) { vm.model?.cancelSleepTimer() }
            }
        } label: {
            Label(
                remaining.map { String(format: "Sleep in %d:%02d", $0 / 60, $0 % 60) } ?? "Sleep timer",
                systemImage: remaining != nil ? "moon.zzz.fill" : "moon.zzz"
            ).font(.footnote)
                .foregroundStyle(remaining != nil ? tint : .secondary)
        }
    }
}

/// Playback position bar with elapsed / remaining time; drag to seek.
struct ScrubberView: View {
    @ObservedObject var vm: PlayerViewModel
    @State private var scrubbing = false
    @State private var value = 0.0

    var body: some View {
        let dur = max(vm.duration, 1)
        VStack(spacing: 2) {
            Slider(value: $value, in: 0...dur) { editing in
                scrubbing = editing
                if !editing { vm.seek(to: value) }
            }
            HStack {
                Text(hms(value))
                Spacer()
                Text("-" + hms(max(dur - value, 0)))
            }
            .font(.caption2).monospacedDigit().foregroundStyle(.secondary)
        }
        .onAppear { value = vm.currentTime }
        .onChange(of: vm.currentTime) { t in if !scrubbing { value = t } }
    }
}

/// Format seconds as m:ss or h:mm:ss.
func hms(_ secs: Double) -> String {
    let s = max(Int(secs.rounded()), 0)
    let h = s / 3600, m = (s % 3600) / 60, sec = s % 60
    return h > 0 ? String(format: "%d:%02d:%02d", h, m, sec)
                 : String(format: "%d:%02d", m, sec)
}

/// Spotify-style running text: highlights the sentence being spoken, auto-scrolls
/// to it, and lets you tap any line to jump there.
struct LyricsView: View {
    @ObservedObject var vm: PlayerViewModel

    var body: some View {
        let currentID = vm.currentLineID()
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    ForEach(vm.transcript) { line in
                        let isCurrent = line.id == currentID
                        Text(line.text)
                            .font(isCurrent ? .title3 : .body)
                            .fontWeight(isCurrent ? .semibold : .regular)
                            .foregroundStyle(isCurrent ? Color.primary : .secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .id(line.id)
                            .contentShape(Rectangle())
                            .onTapGesture { vm.seek(to: line.startS) }
                    }
                }
                .padding(.horizontal)
                .padding(.bottom, 80)
            }
            .onChange(of: vm.currentTime) { _ in
                guard let id = vm.currentLineID() else { return }
                withAnimation(.easeInOut(duration: 0.3)) {
                    proxy.scrollTo(id, anchor: .center)
                }
            }
        }
    }
}

/// "Previously on…" sheet: requests a recap of everything heard so far, shows
/// a spinner while it's generated, then displays the text and plays the audio
/// (in the book's voice). The book is paused by the caller while this is open.
struct RecapSheet: View {
    let api: APIClient
    let bookID: String
    let positionS: Double
    @Environment(\.dismiss) private var dismiss
    @State private var status: RecapStatus?
    @State private var failed: String?
    @State private var player: AVPlayer?

    var body: some View {
        NavigationStack {
            Group {
                if let st = status, st.state == "ready" {
                    ScrollView { Text(st.text ?? "")
                        .frame(maxWidth: .infinity, alignment: .leading).padding() }
                } else if let f = failed ?? status?.error, status?.state == "failed" || failed != nil {
                    VStack(spacing: 10) {
                        Image(systemName: "exclamationmark.bubble").font(.largeTitle).foregroundStyle(.secondary)
                        Text(f).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    }.padding()
                } else {
                    VStack(spacing: 14) {
                        ProgressView()
                        Text("Summarizing what you've heard…").foregroundStyle(.secondary)
                    }.padding(40)
                }
            }
            .navigationTitle("Previously on…")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { player?.pause(); dismiss() }
                }
            }
        }
        .task { await run() }
        .onDisappear { player?.pause(); player = nil }
    }

    private func run() async {
        do {
            let id = try await api.startRecap(bookID: bookID, positionS: positionS)
            for _ in 0..<90 {                       // poll up to ~3 min
                let st = try await api.recapStatus(id)
                status = st
                if st.state == "ready" {
                    if let u = st.audioUrl { playAudio(u) }
                    return
                }
                if st.state == "failed" { return }
                try? await Task.sleep(nanoseconds: 2_000_000_000)
            }
            failed = "Timed out generating the recap."
        } catch { failed = "Couldn't generate a recap." }
    }

    private func playAudio(_ path: String) {
        guard let url = api.absURL(path) else { return }
        let asset = AVURLAsset(url: url, options: [
            "AVURLAssetHTTPHeaderFieldsKey": ["Authorization": "Bearer \(api.settings.token)"]
        ])
        let p = AVPlayer(playerItem: AVPlayerItem(asset: asset))
        player = p
        p.play()
    }
}

/// Bridges the @MainActor view model into a SwiftUI @StateObject lifecycle.
@MainActor final class PlayerViewModelHolder: ObservableObject {
    @Published var model: PlayerViewModel?
    func configure(api: APIClient, book: Book) {
        guard model == nil else { return }
        let m = PlayerViewModel(api: api, book: book)
        model = m
        m.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
        m.start()
    }
    private var cancellables = Set<AnyCancellable>()
}

