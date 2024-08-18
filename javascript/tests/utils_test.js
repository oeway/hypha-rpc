import { expect } from "chai";
import { parseServiceUrl } from "../src/utils/index.js";

describe("Test utilities", async () => {
  it("parse service url", async () => {
    // Test case 1: Basic service URL
    let result = parseServiceUrl(
      "https://hypha.aicell.io/public/services/hypha-login",
    );
    expect(result).to.deep.equal({
      serverUrl: "https://hypha.aicell.io",
      workspace: "public",
      clientId: "*",
      serviceId: "hypha-login",
      appId: "*",
    });

    // Test case 2: Service URL with client_id
    result = parseServiceUrl(
      "https://hypha.aicell.io/public/services/client:hypha-login",
    );
    expect(result).to.deep.equal({
      serverUrl: "https://hypha.aicell.io",
      workspace: "public",
      clientId: "client",
      serviceId: "hypha-login",
      appId: "*",
    });

    // Test case 3: Service URL with app_id
    result = parseServiceUrl(
      "https://hypha.aicell.io/public/services/hypha-login@app",
    );
    expect(result).to.deep.equal({
      serverUrl: "https://hypha.aicell.io",
      workspace: "public",
      clientId: "*",
      serviceId: "hypha-login",
      appId: "app",
    });

    // Test case 4: Service URL with both client_id and app_id
    result = parseServiceUrl(
      "https://hypha.aicell.io/public/services/client:hypha-login@app",
    );
    expect(result).to.deep.equal({
      serverUrl: "https://hypha.aicell.io",
      workspace: "public",
      clientId: "client",
      serviceId: "hypha-login",
      appId: "app",
    });

    // Test case 5: Service URL with trailing slash
    result = parseServiceUrl(
      "https://hypha.aicell.io/public/services/hypha-login/",
    );
    expect(result).to.deep.equal({
      serverUrl: "https://hypha.aicell.io",
      workspace: "public",
      clientId: "*",
      serviceId: "hypha-login",
      appId: "*",
    });

    // Test case 6: Invalid service URL (should throw an error)
    expect(() =>
      parseServiceUrl("https://hypha.aicell.io/public/hypha-login"),
    ).to.throw(Error, "URL does not match the expected pattern");
  }).timeout(20000);
});
