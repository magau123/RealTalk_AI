import CoreMedia
import Foundation
import ScreenCaptureKit

private final class AudioOutput: NSObject, SCStreamOutput {
    private let output = FileHandle.standardOutput

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of type: SCStreamOutputType
    ) {
        guard type == .audio, CMSampleBufferDataIsReady(sampleBuffer) else { return }

        guard
            let description = CMSampleBufferGetFormatDescription(sampleBuffer),
            let streamDescription = CMAudioFormatDescriptionGetStreamBasicDescription(description)
        else { return }

        var bufferList = AudioBufferList(
            mNumberBuffers: 1,
            mBuffers: AudioBuffer(
                mNumberChannels: 0,
                mDataByteSize: 0,
                mData: nil
            )
        )
        var retainedBlockBuffer: CMBlockBuffer?
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: nil,
            bufferListOut: &bufferList,
            bufferListSize: MemoryLayout<AudioBufferList>.size,
            blockBufferAllocator: kCFAllocatorDefault,
            blockBufferMemoryAllocator: kCFAllocatorDefault,
            flags: 0,
            blockBufferOut: &retainedBlockBuffer
        )
        guard status == noErr else { return }

        let asbd = streamDescription.pointee
        let frames = CMSampleBufferGetNumSamples(sampleBuffer)
        guard frames > 0 else { return }

        let buffers = UnsafeMutableAudioBufferListPointer(&bufferList)
        guard let buffer = buffers.first, let data = buffer.mData else { return }
        let channels = max(Int(buffer.mNumberChannels), 1)
        let isFloat = (asbd.mFormatFlags & kAudioFormatFlagIsFloat) != 0
        let isSignedInteger = (asbd.mFormatFlags & kAudioFormatFlagIsSignedInteger) != 0
        let bytesPerSample = Int(asbd.mBitsPerChannel / 8)
        guard bytesPerSample == 2 || (bytesPerSample == 4 && isFloat) else { return }

        let samples = data.assumingMemoryBound(to: UInt8.self)
        var pcm = [Int16]()
        pcm.reserveCapacity(frames)

        for frame in 0..<frames {
            var sum: Float = 0
            for channel in 0..<channels {
                let offset = (frame * channels + channel) * bytesPerSample
                if bytesPerSample == 4 && isFloat {
                    let value = samples.advanced(by: offset).withMemoryRebound(
                        to: Float.self,
                        capacity: 1
                    ) { $0.pointee }
                    sum += value
                } else if bytesPerSample == 2 && isSignedInteger {
                    let value = samples.advanced(by: offset).withMemoryRebound(
                        to: Int16.self,
                        capacity: 1
                    ) { $0.pointee }
                    sum += Float(value) / 32768.0
                } else {
                    return
                }
            }
            let mono = max(-1.0, min(1.0, sum / Float(channels)))
            pcm.append(Int16(mono * 32767.0))
        }

        pcm.withUnsafeBytes { bytes in
            if let baseAddress = bytes.baseAddress {
                output.write(Data(bytes: baseAddress, count: bytes.count))
            }
        }
    }
}

@available(macOS 13.0, *)
private final class CaptureController {
    private let audioOutput = AudioOutput()
    private var stream: SCStream?

    func start() {
        Task {
            do {
                let shareableContent = try await SCShareableContent.excludingDesktopWindows(
                    false,
                    onScreenWindowsOnly: true
                )
                guard let display = shareableContent.displays.first else {
                    throw CaptureError.noDisplay
                }

                let filter = SCContentFilter(display: display, excludingWindows: [])
                let configuration = SCStreamConfiguration()
                configuration.capturesAudio = true
                configuration.sampleRate = 16_000
                configuration.channelCount = 1
                configuration.excludesCurrentProcessAudio = true

                let capture = SCStream(filter: filter, configuration: configuration, delegate: nil)
                try capture.addStreamOutput(
                    audioOutput,
                    type: .audio,
                    sampleHandlerQueue: DispatchQueue(label: "realtalk.system-audio")
                )
                try await capture.startCapture()
                stream = capture
            } catch {
                fputs("RealTalk macOS audio capture: \(error)\n", stderr)
                exit(EXIT_FAILURE)
            }
        }
    }
}

private enum CaptureError: Error {
    case noDisplay
}

if #available(macOS 13.0, *) {
    let controller = CaptureController()
    controller.start()
    dispatchMain()
} else {
    fputs("RealTalk macOS audio capture requires macOS 13 or later.\n", stderr)
    exit(EXIT_FAILURE)
}
