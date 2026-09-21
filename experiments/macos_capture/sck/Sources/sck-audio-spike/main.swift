import AVFoundation
import CoreMedia
import CoreGraphics
import Darwin
import Foundation
import ScreenCaptureKit

private struct Options {
    let duration: Double
    let outputDirectory: URL

    static let usage = """
    Usage: sck-audio-spike --duration <seconds> --output-dir <path>
    Build: swift build -c release
    """

    static func parse() throws -> Options? {
        var duration: Double?
        var outputDirectory: URL?
        var arguments = Array(CommandLine.arguments.dropFirst())
        if arguments == ["--help"] || arguments == ["-h"] {
            print(usage)
            return nil
        }
        while !arguments.isEmpty {
            let option = arguments.removeFirst()
            guard !arguments.isEmpty else { throw SpikeError.usage("missing value for \(option)") }
            let value = arguments.removeFirst()
            switch option {
            case "--duration":
                guard let parsed = Double(value), parsed > 0, parsed.isFinite else {
                    throw SpikeError.usage("--duration must be a finite number greater than zero")
                }
                duration = parsed
            case "--output-dir":
                outputDirectory = URL(fileURLWithPath: value, isDirectory: true).standardizedFileURL
            default:
                throw SpikeError.usage("unknown option: \(option)")
            }
        }
        guard let duration, let outputDirectory else {
            throw SpikeError.usage(usage)
        }
        return Options(duration: duration, outputDirectory: outputDirectory)
    }
}

private enum SpikeError: Error, CustomStringConvertible {
    case usage(String)
    case message(String)

    var description: String {
        switch self {
        case .usage(let text), .message(let text): text
        }
    }
}

private typealias CaptureOutcome = (
    session: CaptureSession,
    reason: String,
    error: Error?
)

private struct TimeEvidence: Encodable {
    let value: Int64
    let timescale: Int32
    let seconds: Double?

    init(_ time: CMTime) {
        value = time.value
        timescale = time.timescale
        let candidate = time.seconds
        seconds = time.isValid && !time.isIndefinite && candidate.isFinite ? candidate : nil
    }

    private enum CodingKeys: String, CodingKey { case value, timescale, seconds }

    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(value, forKey: .value)
        try values.encode(timescale, forKey: .timescale)
        if let seconds { try values.encode(seconds, forKey: .seconds) }
        else { try values.encodeNil(forKey: .seconds) }
    }
}

private struct FormatEvidence: Encodable {
    let sampleRate: Double
    let channels: UInt32
    let formatID: String
    let formatFlags: UInt32
    let bitsPerChannel: UInt32
    let bytesPerFrame: UInt32

    init(_ format: AudioStreamBasicDescription) {
        sampleRate = format.mSampleRate
        channels = format.mChannelsPerFrame
        formatID = String(format: "%c%c%c%c",
            (format.mFormatID >> 24) & 0xff, (format.mFormatID >> 16) & 0xff,
            (format.mFormatID >> 8) & 0xff, format.mFormatID & 0xff)
        formatFlags = format.mFormatFlags
        bitsPerChannel = format.mBitsPerChannel
        bytesPerFrame = format.mBytesPerFrame
    }
}

private struct TrackEvidence: Encodable {
    var sourcePath: String
    var format: FormatEvidence?
    var firstPTS: TimeEvidence?
    var lastPTS: TimeEvidence?
    var firstCallbackUptimeNanoseconds: UInt64?
    var lastCallbackUptimeNanoseconds: UInt64?
    var frameCount: Int64 = 0
    var callbackCount: Int64 = 0
    var maxCallbackGapSeconds: Double = 0
    var buffersWithNonZeroBytes: Int64 = 0
    var buffersWithOnlyZeroBytes: Int64 = 0
    var signalPresent: Bool?
    var silenceOnly: Bool?

    mutating func finalize() {
        guard callbackCount > 0 else {
            signalPresent = nil
            silenceOnly = nil
            return
        }
        signalPresent = buffersWithNonZeroBytes > 0
        silenceOnly = buffersWithNonZeroBytes == 0
    }

    private enum CodingKeys: String, CodingKey {
        case sourcePath, format, firstPTS, lastPTS, firstCallbackUptimeNanoseconds
        case lastCallbackUptimeNanoseconds, frameCount, callbackCount, maxCallbackGapSeconds
        case buffersWithNonZeroBytes, buffersWithOnlyZeroBytes, signalPresent, silenceOnly
    }

    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(sourcePath, forKey: .sourcePath)
        if let format { try values.encode(format, forKey: .format) } else { try values.encodeNil(forKey: .format) }
        if let firstPTS { try values.encode(firstPTS, forKey: .firstPTS) } else { try values.encodeNil(forKey: .firstPTS) }
        if let lastPTS { try values.encode(lastPTS, forKey: .lastPTS) } else { try values.encodeNil(forKey: .lastPTS) }
        if let firstCallbackUptimeNanoseconds {
            try values.encode(firstCallbackUptimeNanoseconds, forKey: .firstCallbackUptimeNanoseconds)
        } else { try values.encodeNil(forKey: .firstCallbackUptimeNanoseconds) }
        if let lastCallbackUptimeNanoseconds {
            try values.encode(lastCallbackUptimeNanoseconds, forKey: .lastCallbackUptimeNanoseconds)
        } else { try values.encodeNil(forKey: .lastCallbackUptimeNanoseconds) }
        try values.encode(frameCount, forKey: .frameCount)
        try values.encode(callbackCount, forKey: .callbackCount)
        try values.encode(maxCallbackGapSeconds, forKey: .maxCallbackGapSeconds)
        try values.encode(buffersWithNonZeroBytes, forKey: .buffersWithNonZeroBytes)
        try values.encode(buffersWithOnlyZeroBytes, forKey: .buffersWithOnlyZeroBytes)
        if let signalPresent { try values.encode(signalPresent, forKey: .signalPresent) }
        else { try values.encodeNil(forKey: .signalPresent) }
        if let silenceOnly { try values.encode(silenceOnly, forKey: .silenceOnly) }
        else { try values.encodeNil(forKey: .silenceOnly) }
    }
}

private struct RelativeStartEvidence: Encodable {
    let microphoneMinusSystemPTSSeconds: Double?
    let microphoneMinusSystemCallbackSeconds: Double?

    private enum CodingKeys: String, CodingKey {
        case microphoneMinusSystemPTSSeconds, microphoneMinusSystemCallbackSeconds
    }

    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        if let microphoneMinusSystemPTSSeconds {
            try values.encode(microphoneMinusSystemPTSSeconds, forKey: .microphoneMinusSystemPTSSeconds)
        } else { try values.encodeNil(forKey: .microphoneMinusSystemPTSSeconds) }
        if let microphoneMinusSystemCallbackSeconds {
            try values.encode(microphoneMinusSystemCallbackSeconds, forKey: .microphoneMinusSystemCallbackSeconds)
        } else { try values.encodeNil(forKey: .microphoneMinusSystemCallbackSeconds) }
    }
}

private struct ResultEvidence: Encodable {
    let schemaVersion = 1
    let candidate = "sck"
    let macOSVersion: String
    let startedAt: String
    let finishedAt: String
    let requestedDurationSeconds: Double
    let observedWallClockDurationSeconds: Double
    let stopReason: String
    let captureLifecycleCompleted: Bool
    let evidencePassed: Bool
    let succeeded: Bool
    let streamOrDelegateError: String?
    let permissions: PermissionEvidence
    let configuration: ConfigurationEvidence
    let system: TrackEvidence
    let microphone: TrackEvidence
    let relativeStart: RelativeStartEvidence

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, candidate, macOSVersion, startedAt, finishedAt
        case requestedDurationSeconds, observedWallClockDurationSeconds, stopReason
        case captureLifecycleCompleted, evidencePassed, succeeded, streamOrDelegateError
        case permissions, configuration, system, microphone, relativeStart
    }

    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(schemaVersion, forKey: .schemaVersion)
        try values.encode(candidate, forKey: .candidate)
        try values.encode(macOSVersion, forKey: .macOSVersion)
        try values.encode(startedAt, forKey: .startedAt)
        try values.encode(finishedAt, forKey: .finishedAt)
        try values.encode(requestedDurationSeconds, forKey: .requestedDurationSeconds)
        try values.encode(observedWallClockDurationSeconds, forKey: .observedWallClockDurationSeconds)
        try values.encode(stopReason, forKey: .stopReason)
        try values.encode(captureLifecycleCompleted, forKey: .captureLifecycleCompleted)
        try values.encode(evidencePassed, forKey: .evidencePassed)
        try values.encode(succeeded, forKey: .succeeded)
        if let streamOrDelegateError {
            try values.encode(streamOrDelegateError, forKey: .streamOrDelegateError)
        } else { try values.encodeNil(forKey: .streamOrDelegateError) }
        try values.encode(permissions, forKey: .permissions)
        try values.encode(configuration, forKey: .configuration)
        try values.encode(system, forKey: .system)
        try values.encode(microphone, forKey: .microphone)
        try values.encode(relativeStart, forKey: .relativeStart)
    }
}

private struct PermissionEvidence: Encodable {
    let screenCapturePreflightPassedBeforeCapture: Bool
    let screenCapturePreflightPassedAfterCapture: Bool
    let screenCaptureAuthorizationAfterCapture: String?
    let microphoneAuthorizationBeforeCapture: String
    let microphoneAuthorizationAfterCapture: String

    private enum CodingKeys: String, CodingKey {
        case screenCapturePreflightPassedBeforeCapture, screenCapturePreflightPassedAfterCapture
        case screenCaptureAuthorizationAfterCapture
        case microphoneAuthorizationBeforeCapture, microphoneAuthorizationAfterCapture
    }

    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(screenCapturePreflightPassedBeforeCapture,
                          forKey: .screenCapturePreflightPassedBeforeCapture)
        try values.encode(screenCapturePreflightPassedAfterCapture,
                          forKey: .screenCapturePreflightPassedAfterCapture)
        if let screenCaptureAuthorizationAfterCapture {
            try values.encode(screenCaptureAuthorizationAfterCapture,
                              forKey: .screenCaptureAuthorizationAfterCapture)
        } else {
            try values.encodeNil(forKey: .screenCaptureAuthorizationAfterCapture)
        }
        try values.encode(microphoneAuthorizationBeforeCapture,
                          forKey: .microphoneAuthorizationBeforeCapture)
        try values.encode(microphoneAuthorizationAfterCapture,
                          forKey: .microphoneAuthorizationAfterCapture)
    }
}

private struct ConfigurationEvidence: Encodable {
    let oneSCStream = true
    let capturesSystemAudio = true
    let capturesMicrophone = true
    let excludesCurrentProcessAudio = true
    let screenWidth = 2
    let screenHeight = 2
    let minimumFrameIntervalSeconds = 1.0
}

private final class AudioTrackWriter {
    private var file: AVAudioFile?
    private let sourceName: String
    private let originalFormat: AudioStreamBasicDescription
    private let originalChannelLayout: Data?

    init(sourceName: String, url: URL, description: CMAudioFormatDescription,
         format: AudioStreamBasicDescription, channelLayout: Data?) throws {
        guard format.mFormatID == kAudioFormatLinearPCM else {
            throw SpikeError.message("unexpected non-PCM ScreenCaptureKit audio format")
        }
        let audioFormat = AVAudioFormat(cmAudioFormatDescription: description)
        self.sourceName = sourceName
        originalFormat = format
        originalChannelLayout = channelLayout
        file = try AVAudioFile(
            forWriting: url, settings: audioFormat.settings,
            commonFormat: audioFormat.commonFormat, interleaved: audioFormat.isInterleaved)
    }

    func append(_ sampleBuffer: CMSampleBuffer, description: CMAudioFormatDescription,
                format: AudioStreamBasicDescription, channelLayout: Data?) throws -> Bool {
        guard let file else {
            throw SpikeError.message("\(sourceName) CAF writer is closed")
        }
        guard Self.compatible(format, originalFormat), channelLayout == originalChannelLayout else {
            throw SpikeError.message("\(sourceName) audio format changed during capture")
        }
        let audioFormat = AVAudioFormat(cmAudioFormatDescription: description)
        let frameCount = AVAudioFrameCount(CMSampleBufferGetNumSamples(sampleBuffer))
        guard let buffer = AVAudioPCMBuffer(pcmFormat: audioFormat, frameCapacity: frameCount) else {
            throw SpikeError.message("could not allocate PCM buffer for \(sourceName)")
        }
        buffer.frameLength = frameCount
        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(
            sampleBuffer, at: 0, frameCount: Int32(frameCount),
            into: buffer.mutableAudioBufferList)
        guard status == noErr else {
            throw SpikeError.message("copying \(sourceName) PCM data failed: \(status)")
        }
        let containsNonZeroByte = UnsafeMutableAudioBufferListPointer(
            buffer.mutableAudioBufferList).contains { audioBuffer in
                guard let data = audioBuffer.mData else { return false }
                return UnsafeRawBufferPointer(
                    start: data, count: Int(audioBuffer.mDataByteSize)).contains { $0 != 0 }
        }
        try file.write(from: buffer)
        return containsNonZeroByte
    }

    func close() {
        // AVAudioFile finalizes its container when released; clearing the strong
        // reference here does that deterministically rather than at process exit.
        file = nil
    }

    private static func compatible(_ lhs: AudioStreamBasicDescription,
                                   _ rhs: AudioStreamBasicDescription) -> Bool {
        lhs.mSampleRate == rhs.mSampleRate &&
        lhs.mFormatID == rhs.mFormatID &&
        lhs.mFormatFlags == rhs.mFormatFlags &&
        lhs.mBytesPerPacket == rhs.mBytesPerPacket &&
        lhs.mFramesPerPacket == rhs.mFramesPerPacket &&
        lhs.mBytesPerFrame == rhs.mBytesPerFrame &&
        lhs.mChannelsPerFrame == rhs.mChannelsPerFrame &&
        lhs.mBitsPerChannel == rhs.mBitsPerChannel
    }
}

private final class AudioTrackRecorder {
    private let lock = NSLock()
    private let sourceName: String
    private var evidence: TrackEvidence
    private var writer: AudioTrackWriter?
    private var finalized = false

    init(sourceName: String, url: URL) {
        self.sourceName = sourceName
        evidence = TrackEvidence(sourcePath: url.path)
    }

    func append(_ sampleBuffer: CMSampleBuffer, arrivalUptimeNanoseconds: UInt64) throws {
        lock.lock()
        defer { lock.unlock() }
        guard !finalized else {
            throw SpikeError.message("\(sourceName) track is already finalized")
        }
        guard let description = CMSampleBufferGetFormatDescription(sampleBuffer),
              let pointer = CMAudioFormatDescriptionGetStreamBasicDescription(description) else {
            throw SpikeError.message("\(sourceName) sample did not contain an ASBD")
        }
        let format = pointer.pointee
        let channelLayout = Self.channelLayoutData(description)
        if writer == nil {
            writer = try AudioTrackWriter(
                sourceName: sourceName, url: URL(fileURLWithPath: evidence.sourcePath),
                description: description, format: format, channelLayout: channelLayout)
        }
        let nonZero = try writer?.append(
            sampleBuffer, description: description, format: format,
            channelLayout: channelLayout) ?? false
        update(sampleBuffer, format: format, now: arrivalUptimeNanoseconds, nonZero: nonZero)
    }

    func finalize() -> TrackEvidence {
        lock.lock()
        defer { lock.unlock() }
        if !finalized {
            finalized = true
            writer?.close()
            writer = nil
        }
        var result = evidence
        result.finalize()
        return result
    }

    private func update(_ sampleBuffer: CMSampleBuffer, format: AudioStreamBasicDescription,
                        now: UInt64, nonZero: Bool) {
        if evidence.format == nil { evidence.format = FormatEvidence(format) }
        let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        if evidence.firstPTS == nil {
            evidence.firstPTS = TimeEvidence(pts)
            evidence.firstCallbackUptimeNanoseconds = now
        }
        if let previous = evidence.lastCallbackUptimeNanoseconds {
            evidence.maxCallbackGapSeconds = max(
                evidence.maxCallbackGapSeconds, Double(now - previous) / 1_000_000_000)
        }
        evidence.lastPTS = TimeEvidence(pts)
        evidence.lastCallbackUptimeNanoseconds = now
        evidence.frameCount += Int64(CMSampleBufferGetNumSamples(sampleBuffer))
        evidence.callbackCount += 1
        if nonZero { evidence.buffersWithNonZeroBytes += 1 }
        else { evidence.buffersWithOnlyZeroBytes += 1 }
    }

    private static func channelLayoutData(_ description: CMAudioFormatDescription) -> Data? {
        var size = 0
        guard let layout = CMAudioFormatDescriptionGetChannelLayout(description, sizeOut: &size),
              size > 0 else { return nil }
        return Data(bytes: layout, count: size)
    }
}

private final class CaptureSession: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    private let lifecycleLock = NSLock()
    private let finish: @Sendable (String, Error?) -> Void
    private let system: AudioTrackRecorder
    private let microphone: AudioTrackRecorder
    private var terminating = false

    init(outputDirectory: URL, finish: @escaping @Sendable (String, Error?) -> Void) {
        self.finish = finish
        system = AudioTrackRecorder(
            sourceName: "system", url: outputDirectory.appendingPathComponent("system.caf"))
        microphone = AudioTrackRecorder(
            sourceName: "microphone", url: outputDirectory.appendingPathComponent("microphone.caf"))
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fail(error)
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of outputType: SCStreamOutputType) {
        guard outputType == .audio || outputType == .microphone else { return }
        let arrivalUptimeNanoseconds = DispatchTime.now().uptimeNanoseconds
        guard sampleBuffer.isValid, CMSampleBufferDataIsReady(sampleBuffer) else { return }
        lifecycleLock.lock()
        let acceptingBuffers = !terminating
        lifecycleLock.unlock()
        guard acceptingBuffers else { return }
        do {
            if outputType == .audio {
                try system.append(sampleBuffer, arrivalUptimeNanoseconds: arrivalUptimeNanoseconds)
            } else {
                try microphone.append(sampleBuffer, arrivalUptimeNanoseconds: arrivalUptimeNanoseconds)
            }
        } catch {
            fail(error)
        }
    }

    private func fail(_ error: Error) {
        lifecycleLock.lock()
        let shouldFinish = !terminating
        terminating = true
        lifecycleLock.unlock()
        if shouldFinish { finish("streamFailure", error) }
    }

    func stopAcceptingBuffers() {
        lifecycleLock.lock()
        terminating = true
        lifecycleLock.unlock()
    }

    func finalize() -> (TrackEvidence, TrackEvidence) {
        (system.finalize(), microphone.finalize())
    }
}

@main
private enum Main {
    static func main() async {
        do {
            guard let options = try Options.parse() else { return }
            try prepareFreshOutputDirectory(options.outputDirectory)
            let started = Date()
            let formatter = ISO8601DateFormatter()
            let permissionBefore = currentPermissionState()

            let outcome: CaptureOutcome
            do {
                outcome = try await runCapture(options: options)
            } catch {
                let session = CaptureSession(outputDirectory: options.outputDirectory) { _, _ in }
                outcome = (session: session, reason: "setupFailure", error: error)
            }
            let finished = Date()
            let permissionAfter = currentPermissionState()
            let permissions = PermissionEvidence(
                screenCapturePreflightPassedBeforeCapture: permissionBefore.screenPreflight,
                screenCapturePreflightPassedAfterCapture: permissionAfter.screenPreflight,
                screenCaptureAuthorizationAfterCapture:
                    permissionAfter.screenPreflight ? "authorized" : nil,
                microphoneAuthorizationBeforeCapture: permissionBefore.microphone,
                microphoneAuthorizationAfterCapture: permissionAfter.microphone)
            // runCapture returns only after stopCapture completes. Stop acceptance
            // again for setup failures, then close both tracks before JSON or exit.
            outcome.session.stopAcceptingBuffers()
            let (system, microphone) = outcome.session.finalize()
            let ptsOffset = offset(microphone.firstPTS?.seconds, system.firstPTS?.seconds)
            let hostOffset = offset(
                microphone.firstCallbackUptimeNanoseconds.map { Double($0) / 1_000_000_000 },
                system.firstCallbackUptimeNanoseconds.map { Double($0) / 1_000_000_000 })
            let lifecycleCompleted = outcome.error == nil &&
                (outcome.reason == "duration" || outcome.reason == "interrupt")
            let systemHasCurrentData = system.callbackCount > 0 && system.frameCount > 0 &&
                FileManager.default.fileExists(atPath: system.sourcePath)
            let microphoneHasCurrentData = microphone.callbackCount > 0 && microphone.frameCount > 0 &&
                FileManager.default.fileExists(atPath: microphone.sourcePath)
            let evidencePassed = lifecycleCompleted && systemHasCurrentData && microphoneHasCurrentData &&
                system.signalPresent == true && microphone.signalPresent == true
            let result = ResultEvidence(
                macOSVersion: ProcessInfo.processInfo.operatingSystemVersionString,
                startedAt: formatter.string(from: started),
                finishedAt: formatter.string(from: finished),
                requestedDurationSeconds: options.duration,
                observedWallClockDurationSeconds: finished.timeIntervalSince(started),
                stopReason: outcome.reason,
                captureLifecycleCompleted: lifecycleCompleted,
                evidencePassed: evidencePassed,
                succeeded: evidencePassed,
                streamOrDelegateError: outcome.error.map { String(describing: $0) },
                permissions: permissions,
                configuration: ConfigurationEvidence(),
                system: system,
                microphone: microphone,
                relativeStart: RelativeStartEvidence(
                    microphoneMinusSystemPTSSeconds: ptsOffset,
                    microphoneMinusSystemCallbackSeconds: hostOffset))
            let data = try JSONEncoder.pretty.encode(result)
            try data.write(to: options.outputDirectory.appendingPathComponent("result.json"), options: .atomic)
            if let error = outcome.error {
                fputs("capture failed: \(error)\n", stderr)
                Foundation.exit(EXIT_FAILURE)
            }
            if !evidencePassed {
                fputs("capture completed, but simultaneous non-silent evidence did not pass\n", stderr)
                Foundation.exit(3)
            }
            print("capture complete: \(options.outputDirectory.path)")
        } catch {
            fputs("error: \(error)\n", stderr)
            Foundation.exit(error is SpikeError ? 2 : 1)
        }
    }

    private static func runCapture(options: Options) async throws
        -> CaptureOutcome {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first else { throw SpikeError.message("no display is available") }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let configuration = SCStreamConfiguration()
        configuration.width = 2
        configuration.height = 2
        configuration.minimumFrameInterval = CMTime(seconds: 1, preferredTimescale: 600)
        configuration.queueDepth = 1
        configuration.capturesAudio = true
        configuration.excludesCurrentProcessAudio = true
        configuration.captureMicrophone = true

        return await withCheckedContinuation { continuation in
            let gate = CompletionGate(continuation: continuation)
            let session = CaptureSession(outputDirectory: options.outputDirectory) { reason, error in
                gate.complete(session: nil, reason: reason, error: error)
            }
            gate.session = session
            let stream = SCStream(filter: filter, configuration: configuration, delegate: session)
            gate.stream = stream
            do {
                try stream.addStreamOutput(session, type: .screen,
                    sampleHandlerQueue: DispatchQueue(label: "sck.screen"))
                try stream.addStreamOutput(session, type: .audio,
                    sampleHandlerQueue: DispatchQueue(label: "sck.system-audio"))
                try stream.addStreamOutput(session, type: .microphone,
                    sampleHandlerQueue: DispatchQueue(label: "sck.microphone"))
            } catch {
                gate.complete(session: session, reason: "setupFailure", error: error)
                return
            }
            gate.installSignalHandler()
            Task {
                do {
                    try await stream.startCapture()
                    gate.armDuration(seconds: options.duration)
                } catch {
                    gate.complete(session: session, reason: "startFailure", error: error)
                }
            }
        }
    }

    private static func offset(_ lhs: Double?, _ rhs: Double?) -> Double? {
        guard let lhs, let rhs else { return nil }
        return lhs - rhs
    }

    private static func prepareFreshOutputDirectory(_ url: URL) throws {
        let manager = FileManager.default
        var isDirectory: ObjCBool = false
        if manager.fileExists(atPath: url.path, isDirectory: &isDirectory) {
            guard isDirectory.boolValue else {
                throw SpikeError.message("output path exists and is not a directory: \(url.path)")
            }
            let contents = try manager.contentsOfDirectory(atPath: url.path)
            guard contents.isEmpty else {
                throw SpikeError.message("output directory must be empty: \(url.path)")
            }
        } else {
            try manager.createDirectory(at: url, withIntermediateDirectories: true)
        }
    }

    private static func currentPermissionState() -> (screenPreflight: Bool, microphone: String) {
        let screenPreflight = CGPreflightScreenCaptureAccess()
        let microphone: String
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: microphone = "authorized"
        case .denied: microphone = "denied"
        case .restricted: microphone = "restricted"
        case .notDetermined: microphone = "notDetermined"
        @unknown default: microphone = "unknown"
        }
        return (screenPreflight, microphone)
    }
}

private final class CompletionGate: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<CaptureOutcome, Never>?
    var session: CaptureSession?
    var stream: SCStream?
    private var timer: DispatchSourceTimer?
    private var signalSource: DispatchSourceSignal?
    private var completed = false

    init(continuation: CheckedContinuation<CaptureOutcome, Never>) {
        self.continuation = continuation
    }

    func armDuration(seconds: Double) {
        let timer = DispatchSource.makeTimerSource(queue: .global())
        timer.schedule(deadline: .now() + seconds)
        timer.setEventHandler { [weak self] in self?.complete(session: nil, reason: "duration", error: nil) }
        self.timer = timer
        timer.resume()
    }

    func installSignalHandler() {
        Darwin.signal(SIGINT, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: SIGINT, queue: .global())
        source.setEventHandler { [weak self] in self?.complete(session: nil, reason: "interrupt", error: nil) }
        signalSource = source
        source.resume()
    }

    func complete(session suppliedSession: CaptureSession?, reason: String, error: Error?) {
        lock.lock()
        guard !completed else { lock.unlock(); return }
        completed = true
        let session = suppliedSession ?? self.session
        let stream = self.stream
        timer?.cancel()
        signalSource?.cancel()
        lock.unlock()
        guard let session else { return }
        session.stopAcceptingBuffers()
        Task {
            var finalError = error
            do { try await stream?.stopCapture() } catch { if finalError == nil { finalError = error } }
            let continuation = self.takeContinuation()
            continuation?.resume(returning: (
                session: session,
                reason: reason,
                error: finalError
            ))
        }
    }

    private func takeContinuation() -> CheckedContinuation<CaptureOutcome, Never>? {
        lock.lock()
        defer { lock.unlock() }

        let result = continuation
        continuation = nil
        return result
    }
}

private extension JSONEncoder {
    static var pretty: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return encoder
    }
}
