import { expect } from "chai";
import { isLoginCompleteMessage } from "../src/websocket-client.js";

// Security-critical: isLoginCompleteMessage decides when the inline (iframe)
// login modal is torn down. It must accept ONLY a well-formed completion signal
// from the expected server origin with the expected session key. The token is
// never carried in the message (it is fetched over the trusted check() RPC), so
// this guards against a hostile frame forging a "done" signal for another session
// or from another origin.
describe("isLoginCompleteMessage", () => {
  const origin = "https://hypha.example.com";
  const key = "session-key-123";
  const validData = { type: "hypha-login-complete", key };

  it("accepts a valid completion message from the expected origin + key", () => {
    expect(
      isLoginCompleteMessage({ origin, data: validData }, origin, key),
    ).to.equal(true);
  });

  it("rejects a message from a different origin (anti-spoofing)", () => {
    expect(
      isLoginCompleteMessage(
        { origin: "https://evil.example", data: validData },
        origin,
        key,
      ),
    ).to.equal(false);
  });

  it("rejects a wrong/missing message type", () => {
    expect(
      isLoginCompleteMessage(
        { origin, data: { type: "something-else", key } },
        origin,
        key,
      ),
    ).to.equal(false);
  });

  it("rejects a mismatched session key", () => {
    expect(
      isLoginCompleteMessage(
        { origin, data: { type: "hypha-login-complete", key: "other-key" } },
        origin,
        key,
      ),
    ).to.equal(false);
  });

  it("rejects non-object / missing / null data", () => {
    expect(isLoginCompleteMessage({ origin, data: null }, origin, key)).to.equal(
      false,
    );
    expect(
      isLoginCompleteMessage(
        { origin, data: "hypha-login-complete" },
        origin,
        key,
      ),
    ).to.equal(false);
    expect(isLoginCompleteMessage(null, origin, key)).to.equal(false);
  });
});
