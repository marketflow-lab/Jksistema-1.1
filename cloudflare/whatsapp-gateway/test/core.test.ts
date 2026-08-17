import { createHmac } from "node:crypto";
import { describe, expect, it } from "vitest";
import {
  compactReply,
  compactReplyParts,
  isFreeWindowOpen,
  isPolicyValid,
  mediaPolicy,
  normalizeSeverity,
  normalizeInternationalPhone,
  phoneAliases,
  outboundRecipient,
  outboundImagePolicy,
  pairingCodeFromText,
  profileImagePolicy,
  safeTemplate,
  verifyMetaSignature,
} from "../src/core";

describe("zero cost policy", () => {
  it("fails closed after policy date", () => {
    expect(isPolicyValid("2026-09-30T23:59:59Z", Date.parse("2026-09-30T20:00:00Z"))).toBe(true);
    expect(isPolicyValid("2026-09-30T23:59:59Z", Date.parse("2026-10-01T00:00:00Z"))).toBe(false);
  });

  it("uses the 23h30 free window", () => {
    expect(isFreeWindowOpen(1000, 1000 + 84600, 84600)).toBe(true);
    expect(isFreeWindowOpen(1000, 1000 + 84601, 84600)).toBe(false);
  });
});

describe("webhook security", () => {
  it("validates Meta HMAC", async () => {
    const secret = "app-secret";
    const raw = new TextEncoder().encode('{"ok":true}');
    const signature = `sha256=${createHmac("sha256", secret).update(raw).digest("hex")}`;
    expect(await verifyMetaSignature(raw.buffer, signature, secret)).toBe(true);
    expect(await verifyMetaSignature(raw.buffer, `${signature}00`, secret)).toBe(false);
  });

});

describe("input policies", () => {
  it("normalizes Brazilian phone aliases", () => {
    expect(normalizeInternationalPhone("+55 (37) 99999-3818")).toBe("5537999993818");
    expect(phoneAliases("+55 (37) 99999-3818")).toEqual(["5537999993818", "553799993818"]);
  });

  it("accepts only eight-character pairing codes", () => {
    expect(pairingCodeFromText("VINCULAR ABCD2345")).toBe("ABCD2345");
    expect(pairingCodeFromText("abcd2345")).toBe("ABCD2345");
    expect(pairingCodeFromText("ABC123")).toBe("");
  });

  it("sends replies to wa_id instead of the BSUID subject", () => {
    expect(outboundRecipient("2313137759491973", "2313137759491973", { wa_id: "553788393818", phone_number: "553788393818" })).toBe("553788393818");
    expect(outboundRecipient("2313137759491973", "2313137759491973", { phone_number: "+55 37 8839-3818" })).toBe("553788393818");
  });

  it("allows only planned media", () => {
    expect(mediaPolicy("image", "image/jpeg").allowed).toBe(true);
    expect(mediaPolicy("audio", "audio/ogg; codecs=opus").allowed).toBe(true);
    expect(mediaPolicy("video", "video/mp4").allowed).toBe(false);
  });

  it("validates WhatsApp business profile images", () => {
    expect(profileImagePolicy("image/jpeg", 120865, new Uint8Array([0xff, 0xd8, 0xff, 0xe0])).allowed).toBe(true);
    expect(profileImagePolicy("image/png", 100, new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])).allowed).toBe(true);
    expect(profileImagePolicy("image/jpeg", 100, new Uint8Array([0x89, 0x50, 0x4e])).error).toBe("profile_image_signature_invalid");
    expect(profileImagePolicy("image/webp", 100, new Uint8Array([0x52, 0x49, 0x46, 0x46])).allowed).toBe(false);
  });

  it("validates outbound WhatsApp images", () => {
    expect(outboundImagePolicy("image/jpeg", 120865, new Uint8Array([0xff, 0xd8, 0xff, 0xe0])).allowed).toBe(true);
    expect(outboundImagePolicy("image/png", 100, new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])).allowed).toBe(true);
    expect(outboundImagePolicy("image/webp", 100, new Uint8Array([0x52, 0x49, 0x46, 0x46])).error).toBe("outbound_image_type_not_allowed");
    expect(outboundImagePolicy("image/jpeg", 6 * 1024 * 1024, new Uint8Array([0xff, 0xd8, 0xff])).error).toBe("outbound_image_size_limit");
    expect(outboundImagePolicy("image/png", 100, new Uint8Array([0xff, 0xd8, 0xff])).error).toBe("outbound_image_signature_invalid");
  });

  it("allows only approved utility templates", () => {
    expect(safeTemplate("jk_joao_tarefa_concluida", "UTILITY", "APPROVED")).toBe(true);
    expect(safeTemplate("jk_black_jhon_nova_pergunta", "UTILITY", "APPROVED")).toBe(true);
    expect(safeTemplate("jk_black_jhon_nova_pergunta_v2", "UTILITY", "APPROVED")).toBe(true);
    expect(safeTemplate("jk_joao_tarefa_concluida", "MARKETING", "APPROVED")).toBe(false);
    expect(safeTemplate("qualquer", "UTILITY", "APPROVED")).toBe(false);
  });

  it("filters proactive severity and compacts replies", () => {
    expect(normalizeSeverity("Crítico")).toBe("critical");
    expect(normalizeSeverity("leve")).toBe("low");
    expect(compactReply("x".repeat(4000))).toHaveLength(3500);
    expect(compactReplyParts(["parte 1", "parte 2"], "fallback")).toEqual(["parte 1", "parte 2"]);
    expect(compactReplyParts([], "unica")).toEqual(["unica"]);
    const historyParts = Array.from({ length: 40 }, (_, index) => `historico ${index + 1}`);
    expect(compactReplyParts(historyParts, "", 0)).toEqual(historyParts);
  });
});
