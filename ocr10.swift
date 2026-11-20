import Foundation
import Vision
import AppKit
import CoreImage

// appleid captcha reader v10:
//  * engine A: whole-image Vision, many preprocessing variants, topCandidates(3), confidence weighted votes
//  * engine B: per-character slice OCR (adaptive binarisation, several thresholds, needs 4..5 slices)
// prints: obs / CANDS / PERCHAR / SLICES / SLVOTES / BEST
let p = CommandLine.arguments[1]
guard let src = CIImage(contentsOf: URL(fileURLWithPath: p)) else { print("NOIMG"); exit(1) }
let ctx = CIContext()

func render(_ img: CIImage, _ s: CGFloat) -> CGImage? {
    let t = img.transformed(by: CGAffineTransform(scaleX: s, y: s))
    return ctx.createCGImage(t, from: t.extent)
}
func cc(_ img: CIImage, _ sat: CGFloat, _ contrast: CGFloat, _ bright: CGFloat) -> CIImage {
    CIFilter(name: "CIColorControls", parameters: [kCIInputImageKey: img, kCIInputSaturationKey: sat, kCIInputContrastKey: contrast, kCIInputBrightnessKey: bright])!.outputImage!
}
func inv(_ img: CIImage) -> CIImage {
    CIFilter(name: "CIColorInvert", parameters: [kCIInputImageKey: img])!.outputImage!
}
func sharpen(_ img: CIImage, _ s: CGFloat) -> CIImage {
    CIFilter(name: "CISharpenLuminance", parameters: [kCIInputImageKey: img, kCIInputSharpnessKey: s])!.outputImage!
}
func clean(_ s: String) -> String { s.filter { ($0.isLetter && $0.isASCII) || $0.isNumber } }
func ocrCG(_ cg: CGImage, acc: Bool) -> [(String, Float)] {
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = acc ? .accurate : .fast
    req.usesLanguageCorrection = false
    req.automaticallyDetectsLanguage = false
    req.minimumTextHeight = 0.0
    let h = VNImageRequestHandler(cgImage: cg, options: [:])
    do { try h.perform([req]) } catch { return [] }
    var out: [(String, Float)] = []
    for o in (req.results ?? []) {
        for c in o.topCandidates(3) {
            let s = clean(c.string)
            if s.count >= 1 { out.append((s, c.confidence)) }
        }
    }
    return out
}

// ---------- engine A: whole image ----------
let g2 = cc(src, 0.0, 2.0, 0.02)
let g35 = cc(src, 0.0, 3.5, 0.02)
var variants: [(String, CIImage)] = [("orig", src), ("g2", g2), ("gi2", inv(g2)),
                                     ("g35", g35), ("gi35", inv(g35)),
                                     ("sharp", sharpen(g2, 1.0)), ("sharpi", inv(sharpen(g2, 1.0)))]
var votes: [String: Double] = [:]
var perchar: [Int: [Int: [Character: Double]]] = [:]   // candidate length -> position -> char -> score
var nobs = 0
for (name, v) in variants {
    for scale in [4.0, 7.0] as [CGFloat] {
        guard let cg = render(v, scale) else { continue }
        for (s, conf) in ocrCG(cg, acc: true) {
            nobs += 1
            print("obs \(name)x\(Int(scale)): \(s)|\(String(format: "%.3f", conf))")
            votes[s, default: 0] += Double(conf)
            if s.count >= 3 && s.count <= 6 {
                let chars = Array(s)
                if chars.count == 4 || chars.count == 5 {
                    for i in 0..<chars.count { perchar[chars.count, default: [:]][i, default: [:]][chars[i], default: 0] += Double(conf) }
                }
            }
        }
    }
}
let ranked = votes.sorted { $0.value > $1.value }
print("NOBS:\(nobs)")
print("CANDS:" + ranked.prefix(6).map { "\($0.key)|\(String(format: "%.2f", $0.value))" }.joined(separator: ","))
for L in perchar.keys.sorted() {
    var pcOut: [String] = []
    for i in perchar[L]!.keys.sorted() {
        let m = perchar[L]![i]!.sorted { $0.value > $1.value }
        pcOut.append("p\(i+1)=" + m.prefix(3).map { "\($0.key):\(String(format: "%.1f", $0.value))" }.joined(separator: "/"))
    }
    print("PERCHAR\(L):" + pcOut.joined(separator: ";"))
}
print("BEST:" + (ranked.first?.key ?? ""))

// ---------- engine B: per-character slices ----------
func grayBuf(_ cg: CGImage) -> (w: Int, h: Int, buf: [UInt8])? {
    let w = cg.width, h = cg.height
    var buf = [UInt8](repeating: 255, count: w * h)
    guard let cs = CGColorSpace(name: CGColorSpace.linearGray),
          let g = CGContext(data: &buf, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w,
                            space: cs, bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return nil }
    g.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
    return (w, h, buf)
}
var bestSeg: (Int, Bool, [(Int, Int)], CGImage)? = nil
for inverted in [false, true] {
    let base = inverted ? inv(g35) : g35
    guard let cg = render(base, 4.0), let (w, h, buf) = grayBuf(cg) else { continue }
    for thr in [100, 125, 150, 175] {
        var col = [Int](repeating: 0, count: w)
        for x in 0..<w {
            var c = 0
            for y in 0..<h {
                let px = Int(buf[y*w + x])
                let ink = inverted ? (px > thr) : (px < thr)
                if ink { c += 1 }
            }
            col[x] = c
        }
        var segs: [(Int, Int)] = []
        var st = -1
        for x in 0..<w {
            let dark = col[x] >= 2
            if dark && st < 0 { st = x }
            if !dark && st >= 0 { if x - st >= 3 { segs.append((st, x - 1)) }; st = -1 }
        }
        if st >= 0 && w - st >= 3 { segs.append((st, w - 1)) }
        if segs.count == 4 || segs.count == 5 {
            if bestSeg == nil || abs(segs.count - 4) < abs(bestSeg!.2.count - 4) { bestSeg = (thr, inverted, segs, cg) }
        }
    }
}
if let (thr, inverted, segs, cg) = bestSeg {
    print("SLICES:thr=\(thr),inv=\(inverted)," + segs.map { "\($0.0)-\($0.1)" }.joined(separator: ","))
    var slout = ""
    for (a, b) in segs {
        let pad = 3
        let x0 = max(0, a - pad), x1 = min(cg.width, b + pad)
        guard let crop = cg.cropping(to: CGRect(x: x0, y: 0, width: x1 - x0, height: cg.height)) else { continue }
        let up = CIImage(cgImage: crop).transformed(by: CGAffineTransform(scaleX: 5, y: 5))
        guard let upcg = ctx.createCGImage(up, from: up.extent) else { continue }
        let cands = ocrCG(upcg, acc: true)
        let best = cands.first?.0 ?? ""
        print("SLICE \(a)-\(b): " + cands.prefix(2).map { "\($0.0)|\(String(format: "%.2f", $0.1))" }.joined(separator: ","))
        if let f = best.first { slout.append(f) }
    }
    print("SLVOTES:" + slout)
} else {
    print("SLICES:none")
    print("SLVOTES:")
}
