import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

private struct Options {
    let duration: Double
    let outputDirectory: URL

    static func parse() throws -> Options {
        var duration: Double?
        var outputDirectory: URL?
        var arguments = Array(CommandLine.arguments.dropFirst())
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
            throw SpikeError.usage("usage: sck-audio-spike --duration <seconds> --output-dir <path>")
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

private struct TimeEvidence: Codable {
    let value: Int64
    let timescale: Int32
    let seconds: Double

    init(_ time: CMTime) {
        value = time.value
        timescale = time.timescale
        seconds = time.seconds
    }
}

private struct FormatEvidence: Codable {
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

private struct TrackEvidence: Codable {
    var file: String
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
}

private struct RelativeStartEvidence: Codable {
    let microphoneMinusSystemPTSSeconds: Double?
    let microphoneMinusSystemCallbackSeconds: Double?
}

private struct ResultEvidence: Codable {
    let schemaVersion = 1
    let startedAt: String
    let finishedAt: String
    let requestedDurationSeconds: Double
    let stopReason: String
    let succeeded: Bool
    let error: String?
    let configuration: ConfigurationEvidence
    let system: TrackEvidence
    let microphone: TrackEvidence
    let relativeStart: RelativeStartEvidence
}

private struct ConfigurationEvidence: Codable {
    let oneSCStream = true
    let capturesSystemAudio = true
    let capturesMicrophone = true
    let excludesCurrentProcessAudio = true
    let screenWidth = 2
    let screenHeight = 2
    let minimumFrameIntervalSeconds = 1.0
}

private final class AudioTrackWriter {
    private var file: ExtAudioFileRef?

    init(url: URL, format: AudioStreamBasicDescription) throws {
        guard format.mFormatID == kAudioFormatLinearPCM else {
            throw SpikeError.message("unexpected non-PCM ScreenCaptureKit audio format")
        }
        var description = format
        var output: ExtAudioFileRef?
        let createStatus = ExtAudioFileCreateWithURL(
            url as CFURL, kAudioFileCAFType, &description, nil,
            AudioFileFlags.eraseFile.rawValue, &output)
        guard createStatus == noErr, let output else {
            throw SpikeError.message("ExtAudioFileCreateWithURL failed: \(createStatus)")
        }
        file = output
        let clientStatus = ExtAudioFileSetProperty(
            output, kExtAudioFileProperty_ClientDataFormat,
            UInt32(MemoryLayout<AudioStreamBasicDescription>.size), &description)
        guard clientStatus == noErr else {
            ExtAudioFileDispose(output)
            file = nil
            throw SpikeError.message("ExtAudioFile client format failed: \(clientStatus)")
        }
    }

    func append(_ sampleBuffer: CMSampleBuffer, format: AudioStreamBasicDescription) throws -> Bool {
        guard let file else { throw SpikeError.message("audio file is not open") }
        let buffers = AudioBufferList.allocate(maximumBuffers: max(1, Int(format.mChannelsPerFrame)))
        defer { free(buffers.unsafeMutablePointer) }
        var retainedBlockBuffer: CMBlockBuffer?
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer, bufferListSizeNeededOut: nil,
            bufferListOut: buffers.unsafeMutablePointer,
            bufferListSize: buffers.sizeInBytes,
            blockBufferAllocator: kCFAllocatorDefault,
            blockBufferMemoryAllocator: kCFAllocatorDefault,
            flags: UInt32(kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment),
            blockBufferOut: &retainedBlockBuffer)
        guard status == noErr else {
            throw SpikeError.message("extracting audio buffers failed: \(status)")
        }
        let containsNonZeroByte = buffers.contains { buffer in
            guard let data = buffer.mData else { return false }
            return UnsafeRawBufferPointer(start: data, count: Int(buffer.mDataByteSize)).contains { $0 != 0 }
        }
        let frames = UInt32(CMSampleBufferGetNumSamples(sampleBuffer))
        let writeStatus = ExtAudioFileWrite(file, frames, buffers.unsafePointer)
        guard writeStatus == noErr else {
            throw SpikeError.message("writing CAF failed: \(writeStatus)")
        }
        _ = retainedBlockBuffer
        return containsNonZeroByte
    }

    func close() {
        if let file { ExtAudioFileDispose(file) }
        file = nil
    }

    deinit { close() }
}

private final class CaptureSession: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    private let lock = NSLock()
    private let outputDirectory: URL
    private let finish: @Sendable (String, Error?) -> Void
    private var system = TrackEvidence(file: "system.caf")
    private var microphone = TrackEvidence(file: "microphone.caf")
    private var systemWriter: AudioTrackWriter?
    private var microphoneWriter: AudioTrackWriter?
    private var terminating = false

    init(outputDirectory: URL, finish: @escaping @Sendable (String, Error?) -> Void) {
        self.outputDirectory = outputDirectory
        self.finish = finish
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fail(error)
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of outputType: SCStreamOutputType) {
        guard outputType == .audio || outputType == .microphone else { return }
        guard sampleBuffer.isValid, CMSampleBufferDataIsReady(sampleBuffer) else { return }
        lock.lock()
        defer { lock.unlock() }
        guard !terminating else { return }
        do {
            guard let description = CMSampleBufferGetFormatDescription(sampleBuffer),
                  let pointer = CMAudioFormatDescriptionGetStreamBasicDescription(description) else {
                throw SpikeError.message("audio sample did not contain an ASBD")
            }
            let format = pointer.pointee
            let now = DispatchTime.now().uptimeNanoseconds
            if outputType == .audio {
                if systemWriter == nil {
                    systemWriter = try AudioTrackWriter(
                        url: outputDirectory.appendingPathComponent(system.file), format: format)
                }
                let nonZero = try systemWriter?.append(sampleBuffer, format: format) ?? false
                update(&system, sampleBuffer: sampleBuffer, format: format, now: now, nonZero: nonZero)
            } else {
                if microphoneWriter == nil {
                    microphoneWriter = try AudioTrackWriter(
                        url: outputDirectory.appendingPathComponent(microphone.file), format: format)
                }
                let nonZero = try microphoneWriter?.append(sampleBuffer, format: format) ?? false
                update(&microphone, sampleBuffer: sampleBuffer, format: format, now: now, nonZero: nonZero)
            }
        } catch {
            terminating = true
            DispatchQueue.global().async { self.finish("failure", error) }
        }
    }

    private func update(_ evidence: inout TrackEvidence, sampleBuffer: CMSampleBuffer,
                        format: AudioStreamBasicDescription, now: UInt64, nonZero: Bool) {
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
        if nonZero {
            evidence.buffersWithNonZeroBytes += 1
        } else {
            evidence.buffersWithOnlyZeroBytes += 1
        }
    }

    private func fail(_ error: Error) {
        lock.lock()
        let shouldFinish = !terminating
        terminating = true
        lock.unlock()
        if shouldFinish { finish("streamFailure", error) }
    }

    func stopAcceptingBuffers() {
        lock.lock()
        terminating = true
        lock.unlock()
    }

    func snapshot() -> (TrackEvidence, TrackEvidence) {
        lock.lock()
        defer { lock.unlock() }
        systemWriter?.close()
        microphoneWriter?.close()
        return (system, microphone)
    }
}

@main
private enum Main {
    static func main() async {
        do {
            let options = try Options.parse()
            try FileManager.default.createDirectory(
                at: options.outputDirectory, withIntermediateDirectories: true)
            let started = Date()
            let formatter = ISO8601DateFormatter()

            let outcome = try await runCapture(options: options)
            let (system, microphone) = outcome.session.snapshot()
            let ptsOffset = offset(microphone.firstPTS?.seconds, system.firstPTS?.seconds)
            let hostOffset = offset(
                microphone.firstCallbackUptimeNanoseconds.map { Double($0) / 1_000_000_000 },
                system.firstCallbackUptimeNanoseconds.map { Double($0) / 1_000_000_000 })
            let result = ResultEvidence(
                startedAt: formatter.string(from: started),
                finishedAt: formatter.string(from: Date()),
                requestedDurationSeconds: options.duration,
                stopReason: outcome.reason,
                succeeded: outcome.error == nil,
                error: outcome.error.map { String(describing: $0) },
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
            print("capture complete: \(options.outputDirectory.path)")
        } catch {
            fputs("error: \(error)\n", stderr)
            Foundation.exit(error is SpikeError ? 2 : 1)
        }
    }

    private static func runCapture(options: Options) async throws
        -> (session: CaptureSession, reason: String, error: Error?) {
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
}

private final class CompletionGate: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<(CaptureSession, String, Error?), Never>?
    var session: CaptureSession?
    var stream: SCStream?
    private var timer: DispatchSourceTimer?
    private var signal: DispatchSourceSignal?
    private var completed = false

    init(continuation: CheckedContinuation<(CaptureSession, String, Error?), Never>) {
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
        signal(SIGINT, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: SIGINT, queue: .global())
        source.setEventHandler { [weak self] in self?.complete(session: nil, reason: "interrupt", error: nil) }
        signal = source
        source.resume()
    }

    func complete(session suppliedSession: CaptureSession?, reason: String, error: Error?) {
        lock.lock()
        guard !completed else { lock.unlock(); return }
        completed = true
        let session = suppliedSession ?? self.session
        let stream = self.stream
        timer?.cancel()
        signal?.cancel()
        lock.unlock()
        guard let session else { return }
        session.stopAcceptingBuffers()
        Task {
            var finalError = error
            do { try await stream?.stopCapture() } catch { if finalError == nil { finalError = error } }
            lock.lock()
            let continuation = self.continuation
            self.continuation = nil
            lock.unlock()
            continuation?.resume(returning: (session, reason, finalError))
        }
    }
}

private extension JSONEncoder {
    static var pretty: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return encoder
    }
}
