import { expect } from "chai";
import { connectToServer } from "../src/websocket-client.js";

const SERVER_URL = "http://127.0.0.1:9394";

// ============================================================================
// TEST DATA GENERATORS
// ============================================================================

function generateTestData(sizeMB) {
  /** Generate test data of specified size in MB. */
  const sizeBytes = Math.floor(sizeMB * 1024 * 1024);
  let data = new Uint8Array(sizeBytes);
  
  for (let i = 0; i < sizeBytes; i++) {
    data[i] = 120; // ASCII 'x'
  }
  
  return data;
}

function generateTypedArray(shape) {
  /** Generate typed array with specified shape (total elements). */
  const totalElements = shape.reduce((a, b) => a * b, 1);
  return new Float32Array(totalElements).map(() => Math.random());
}

function generateLargeString(sizeMB) {
  /** Generate large string data. */
  const sizeBytes = Math.floor(sizeMB * 1024 * 1024);
  return "x".repeat(sizeBytes);
}

// ============================================================================
// TEST CATEGORY 1: BASIC HTTP TRANSMISSION SETUP
// ============================================================================

describe("HTTP Message Transmission", () => {
  let httpClient;

  before(async function() {
    // Increase timeout for CI environments
    this.timeout(60000);
    
    // Connect once for all tests
    httpClient = await connectToServer({
      name: "http-test-client",
      server_url: SERVER_URL,
      enable_http_transmission: true,
      http_transmission_threshold: 1024 * 1024, // 1MB
      multipart_threshold: 10 * 1024 * 1024, // 10MB
      multipart_size: 6 * 1024 * 1024, // 6MB
      max_parallel_uploads: 5,
    });
    
    // Wait for S3 service to be available
    console.log("⏳ Waiting for S3 service to initialize...");
    let s3Available = false;
    const maxAttempts = 30;
    
    for (let i = 0; i < maxAttempts; i++) {
      try {
        // Use the workspace manager directly which is the httpClient itself
        await httpClient.getService("public/s3-storage", { timeout: 3 });
        s3Available = true;
        console.log("✅ S3 service is available!");
        break;
      } catch (error) {
        console.log(`⏳ S3 not ready yet (attempt ${i + 1}/${maxAttempts}): ${error.message}`);
        // Wait a bit before retrying
        await new Promise(resolve => setTimeout(resolve, 2000));
      }
    }
    
    if (!s3Available) {
      console.warn("⚠️  S3 service is not available - HTTP message transmission tests will be skipped");
    }
  });

  after(async () => {
    if (httpClient) {
      await httpClient.disconnect();
    }
  });

  it("should properly initialize HTTP transmission configuration", async () => {
    console.log("\n=== TESTING HTTP TRANSMISSION AVAILABILITY ===");
    
    // Check if HTTP transmission configuration exists
    expect(httpClient.rpc._enable_http_transmission).to.be.a("boolean");
    expect(httpClient.rpc._http_transmission_threshold).to.be.a("number");
    expect(httpClient.rpc._multipart_threshold).to.be.a("number");
    expect(httpClient.rpc._http_message_transmission_available).to.be.a("boolean");
    
    console.log(`✅ HTTP transmission available: ${httpClient.rpc._http_message_transmission_available}`);
    console.log(`✅ HTTP transmission threshold: ${httpClient.rpc._http_transmission_threshold / (1024*1024)}MB`);
    console.log(`✅ Multipart threshold: ${httpClient.rpc._multipart_threshold / (1024*1024)}MB`);
    
    if (httpClient.rpc._http_message_transmission_available) {
      console.log("✅ S3 controller is properly initialized");
    } else {
      console.log("⚠️  HTTP transmission not available (this is normal if S3 is not configured)");
    }
  }).timeout(30000);

  it("should use regular WebSocket transmission for small messages below threshold", async () => {
    console.log("\n=== TESTING SMALL MESSAGE (BELOW 1MB THRESHOLD) ===");
    
    // Track HTTP transmission events
    const httpTransmissionEvents = [];
    
    const onHttpTransmission = (data) => {
      httpTransmissionEvents.push(data);
      console.log(`🔗 HTTP transmission event: ${(data.content_length / (1024*1024)).toFixed(1)}MB`);
    };
    
    httpClient.rpc.on("http_transmission_stats", onHttpTransmission);
    
    // Register a service that echoes data
    const echoData = (data) => {
      return data;
    };
    
    const serviceInfo = await httpClient.registerService({
      id: "echo-service",
      name: "Echo Service",
      config: { visibility: "public" },
      echoData: echoData
    });
    
    console.log(`✅ Service registered: ${serviceInfo.id}`);
    
    // Test with small data (500KB - well below 1MB threshold)
    const smallData = generateTestData(0.5); // 500KB
    console.log(`📤 Sending ${(smallData.byteLength / (1024*1024)).toFixed(1)}MB of data...`);
    
    const startTime = Date.now();
    const service = await httpClient.getService("echo-service");
    const result = await service.echoData(smallData);
    const transmissionTime = (Date.now() - startTime) / 1000;
    
    console.log(`✅ Data echoed successfully in ${transmissionTime.toFixed(2)}s`);
    expect(result).to.deep.equal(smallData);
    
    // Verify NO HTTP transmission was used
    expect(httpTransmissionEvents).to.have.length(0, "HTTP transmission should not be used for small messages");
    console.log("✅ Confirmed: Small message used regular WebSocket transmission");
  }).timeout(30000);

  it("should use single upload HTTP transmission for medium messages", async function() {
    console.log("\n=== TESTING MEDIUM MESSAGE (1MB-10MB, SINGLE UPLOAD) ===");
    
    // Verify S3 is available before testing HTTP transmission
    if (!httpClient.rpc._http_message_transmission_available) {
      console.warn("⚠️  S3/HTTP message transmission is not available - skipping test");
      this.skip();
      return;
    }
    
    // Track HTTP transmission events
    const httpTransmissionEvents = [];
    
    const onHttpTransmission = (data) => {
      httpTransmissionEvents.push(data);
      console.log(`🔗 HTTP transmission event: ${(data.content_length / (1024*1024)).toFixed(1)}MB`);
      console.log(`   Method: ${data.transmission_method}, Parts: ${data.part_count}`);
      console.log(`   Used multipart: ${data.used_multipart}`);
    };
    
    httpClient.rpc.on("http_transmission_stats", onHttpTransmission);
    
    // Register a service that processes data
    const processData = (data) => {
      return { received_size: data.byteLength, processed: true };
    };
    
    const serviceInfo = await httpClient.registerService({
      id: "process-service",
      name: "Process Service",
      config: { visibility: "public" },
      processData: processData
    });
    
    console.log(`✅ Service registered: ${serviceInfo.id}`);
    
    // Test with medium data (5MB - above 1MB threshold, below 10MB multipart threshold)
    const mediumData = generateTestData(5); // 5MB
    console.log(`📤 Sending ${(mediumData.byteLength / (1024*1024)).toFixed(1)}MB of data...`);
    
    const startTime = Date.now();
    const service = await httpClient.getService("process-service");
    const result = await service.processData(mediumData);
    const transmissionTime = (Date.now() - startTime) / 1000;
    
    console.log(`✅ Data processed successfully in ${transmissionTime.toFixed(2)}s`);
    expect(result.received_size).to.equal(mediumData.byteLength);
    expect(result.processed).to.be.true;
    
    // Verify HTTP transmission was used with single upload
    expect(httpTransmissionEvents).to.have.length(1, "HTTP transmission should be used for medium messages");
    const event = httpTransmissionEvents[0];
    expect(event.transmission_method).to.equal("single_upload");
    expect(event.part_count).to.equal(1);
    expect(event.used_multipart).to.be.false;
    expect(event.multipart_threshold).to.equal(10 * 1024 * 1024); // 10MB
    console.log("✅ Confirmed: Medium message used single upload HTTP transmission");
  }).timeout(30000);

  it("should use multipart upload HTTP transmission for large messages", async function() {
    console.log("\n=== TESTING LARGE MESSAGE (ABOVE 10MB, MULTIPART UPLOAD) ===");
    
    // Verify S3 is available before testing HTTP transmission
    if (!httpClient.rpc._http_message_transmission_available) {
      console.warn("⚠️  S3/HTTP message transmission is not available - skipping test");
      this.skip();
      return;
    }
    
    // Track HTTP transmission events
    const httpTransmissionEvents = [];
    
    const onHttpTransmission = (data) => {
      httpTransmissionEvents.push(data);
      console.log(`🔗 HTTP transmission event: ${(data.content_length / (1024*1024)).toFixed(1)}MB`);
      console.log(`   Method: ${data.transmission_method}, Parts: ${data.part_count}`);
      console.log(`   Used multipart: ${data.used_multipart}`);
      console.log(`   Part size: ${(data.part_size / (1024*1024)).toFixed(1)}MB`);
    };
    
    httpClient.rpc.on("http_transmission_stats", onHttpTransmission);
    
    // Register a service that analyzes data
    const analyzeData = (data) => {
      return {
        size_mb: data.byteLength / (1024 * 1024),
        analyzed: true,
        checksum: data.byteLength % 1000000 // Simple checksum
      };
    };
    
    const serviceInfo = await httpClient.registerService({
      id: "analyze-service",
      name: "Analyze Service",
      config: { visibility: "public" },
      analyzeData: analyzeData
    });
    
    console.log(`✅ Service registered: ${serviceInfo.id}`);
    
    // Test with large data (15MB - above 10MB multipart threshold)
    const largeData = generateTestData(15); // 15MB
    console.log(`📤 Sending ${(largeData.byteLength / (1024*1024)).toFixed(1)}MB of data...`);
    
    const startTime = Date.now();
    const service = await httpClient.getService("analyze-service");
    const result = await service.analyzeData(largeData);
    const transmissionTime = (Date.now() - startTime) / 1000;
    
    console.log(`✅ Data analyzed successfully in ${transmissionTime.toFixed(2)}s`);
    expect(result.size_mb).to.be.closeTo(15.0, 0.1);
    expect(result.analyzed).to.be.true;
    
    // Verify HTTP transmission was used with multipart upload
    expect(httpTransmissionEvents).to.have.length(1, "HTTP transmission should be used for large messages");
    const event = httpTransmissionEvents[0];
    expect(event.transmission_method).to.equal("multipart_upload");
    expect(event.used_multipart).to.be.true;
    expect(event.multipart_threshold).to.equal(10 * 1024 * 1024); // 10MB
    expect(event.part_size).to.equal(6 * 1024 * 1024); // 6MB per part
    // Verify multipart upload was used (actual part count may vary due to data size)
    expect(event.part_count).to.be.at.least(3, `Expected at least 3 parts for 15MB, got ${event.part_count}`);
    console.log("✅ Confirmed: Large message used multipart upload HTTP transmission");
  }).timeout(60000);

  it("should handle typed array transmission", async function() {
    console.log("\n=== TESTING TYPED ARRAY TRANSMISSION ===");
    
    // Verify S3 is available before testing HTTP transmission
    if (!httpClient.rpc._http_message_transmission_available) {
      console.warn("⚠️  S3/HTTP message transmission is not available - skipping test");
      this.skip();
      return;
    }
    
    // Track HTTP transmission events
    const httpTransmissionEvents = [];
    
    const onHttpTransmission = (data) => {
      httpTransmissionEvents.push(data);
      console.log(`🔗 HTTP transmission event: ${(data.content_length / (1024*1024)).toFixed(1)}MB`);
      console.log(`   Method: ${data.transmission_method}, Parts: ${data.part_count}`);
      console.log(`   Used multipart: ${data.used_multipart}`);
    };
    
    httpClient.rpc.on("http_transmission_stats", onHttpTransmission);
    
    // Register a service that processes typed arrays
    const processArray = (arrayData) => {
      return {
        length: arrayData.length,
        byteLength: arrayData.byteLength,
        size_mb: arrayData.byteLength / (1024 * 1024),
        mean: arrayData.reduce((a, b) => a + b, 0) / arrayData.length,
        sum: arrayData.reduce((a, b) => a + b, 0)
      };
    };
    
    const serviceInfo = await httpClient.registerService({
      id: "array-service",
      name: "Array Processing Service",
      config: { visibility: "public" },
      processArray: processArray
    });
    
    console.log(`✅ Service registered: ${serviceInfo.id}`);
    
    // Create large typed array (20MB - should trigger multipart upload)
    const largeArray = generateTypedArray([2048, 2048, 2]); // ~32MB
    console.log(`📤 Sending typed array: ${largeArray.length} elements, ${(largeArray.byteLength / (1024*1024)).toFixed(1)}MB`);
    
    const startTime = Date.now();
    const service = await httpClient.getService("array-service");
    const result = await service.processArray(largeArray);
    const transmissionTime = (Date.now() - startTime) / 1000;
    
    console.log(`✅ Array processed successfully in ${transmissionTime.toFixed(2)}s`);
    expect(result.length).to.equal(2048 * 2048 * 2);
    expect(result.size_mb).to.be.closeTo(32.0, 0.1);
    
    // Verify HTTP transmission was used
    expect(httpTransmissionEvents).to.have.length(1, "HTTP transmission should be used for large typed arrays");
    const event = httpTransmissionEvents[0];
    expect(event.transmission_method).to.equal("multipart_upload");
    expect(event.used_multipart).to.be.true;
    // Verify multipart upload was used (actual part count may vary due to data size)
    expect(event.part_count).to.be.at.least(6, `Expected at least 6 parts for 32MB, got ${event.part_count}`);
    console.log("✅ Confirmed: Large typed array used multipart upload HTTP transmission");
  }).timeout(60000);

  it("should handle string data transmission", async function() {
    console.log("\n=== TESTING LARGE STRING TRANSMISSION ===");
    
    // Verify S3 is available before testing HTTP transmission
    if (!httpClient.rpc._http_message_transmission_available) {
      console.warn("⚠️  S3/HTTP message transmission is not available - skipping test");
      this.skip();
      return;
    }
    
    // Track HTTP transmission events
    const httpTransmissionEvents = [];
    
    const onHttpTransmission = (data) => {
      httpTransmissionEvents.push(data);
      console.log(`🔗 HTTP transmission event: ${(data.content_length / (1024*1024)).toFixed(1)}MB`);
      console.log(`   Method: ${data.transmission_method}, Parts: ${data.part_count}`);
      console.log(`   Used multipart: ${data.used_multipart}`);
    };
    
    httpClient.rpc.on("http_transmission_stats", onHttpTransmission);
    
    // Register a service that processes string data
    const processString = (stringData) => {
      return {
        length: stringData.length,
        size_mb: new TextEncoder().encode(stringData).length / (1024 * 1024),
        first_char: stringData[0],
        last_char: stringData[stringData.length - 1],
        char_count: new Set(stringData).size
      };
    };
    
    const serviceInfo = await httpClient.registerService({
      id: "string-service",
      name: "String Processing Service",
      config: { visibility: "public" },
      processString: processString
    });
    
    console.log(`✅ Service registered: ${serviceInfo.id}`);
    
    // Create large string data (12MB - should trigger multipart upload)
    const largeString = generateLargeString(12); // 12MB
    console.log(`📤 Sending large string: ${(new TextEncoder().encode(largeString).length / (1024*1024)).toFixed(1)}MB`);
    
    const startTime = Date.now();
    const service = await httpClient.getService("string-service");
    const result = await service.processString(largeString);
    const transmissionTime = (Date.now() - startTime) / 1000;
    
    console.log(`✅ String processed successfully in ${transmissionTime.toFixed(2)}s`);
    expect(result.length).to.equal(largeString.length);
    expect(result.size_mb).to.be.closeTo(12.0, 0.1);
    expect(result.first_char).to.equal("x");
    expect(result.last_char).to.equal("x");
    
    // Verify HTTP transmission was used
    expect(httpTransmissionEvents).to.have.length(1, "HTTP transmission should be used for large strings");
    const event = httpTransmissionEvents[0];
    expect(event.transmission_method).to.equal("multipart_upload");
    expect(event.used_multipart).to.be.true;
    // Verify multipart upload was used (actual part count may vary due to data size)
    expect(event.part_count).to.be.at.least(3, `Expected at least 3 parts for 12MB, got ${event.part_count}`);
    console.log("✅ Confirmed: Large string used multipart upload HTTP transmission");
  }).timeout(60000);

  it("should fall back to WebSocket when HTTP transmission fails", async () => {
    console.log("\n=== TESTING HTTP TRANSMISSION FALLBACK ===");
    
    // Temporarily disable HTTP transmission to test fallback
    const originalAvailable = httpClient.rpc._http_message_transmission_available;
    httpClient.rpc._http_message_transmission_available = false;
    
    // Register a service
    const echoData = (data) => {
      return { echoed: true, size: data.byteLength || data.length };
    };
    
    const serviceInfo = await httpClient.registerService({
      id: "fallback-service",
      name: "Fallback Service",
      config: { visibility: "public" },
      echoData: echoData
    });
    
    console.log(`✅ Service registered: ${serviceInfo.id}`);
    
    // Test with large data that would normally use HTTP transmission
    const largeData = generateTestData(5); // 5MB
    console.log(`📤 Sending ${(largeData.byteLength / (1024*1024)).toFixed(1)}MB with HTTP disabled...`);
    
    const startTime = Date.now();
    const service = await httpClient.getService("fallback-service");
    const result = await service.echoData(largeData);
    const transmissionTime = (Date.now() - startTime) / 1000;
    
    console.log(`✅ Data transmitted successfully via fallback in ${transmissionTime.toFixed(2)}s`);
    expect(result.echoed).to.be.true;
    expect(result.size).to.equal(largeData.byteLength);
    
    // Restore original HTTP transmission availability
    httpClient.rpc._http_message_transmission_available = originalAvailable;
    
    console.log("✅ Confirmed: System gracefully falls back to WebSocket when HTTP transmission is unavailable");
  }).timeout(60000);

  it("should handle concurrent large transmissions", async function() {
    console.log("\n=== TESTING CONCURRENT LARGE TRANSMISSIONS ===");
    
    // Verify S3 is available before testing HTTP transmission
    if (!httpClient.rpc._http_message_transmission_available) {
      console.warn("⚠️  S3/HTTP message transmission is not available - skipping test");
      this.skip();
      return;
    }
    
    // Track HTTP transmission events
    const httpTransmissionEvents = [];
    
    const onHttpTransmission = (data) => {
      httpTransmissionEvents.push(data);
      console.log(`🔗 HTTP transmission event: ${(data.content_length / (1024*1024)).toFixed(1)}MB`);
      console.log(`   Method: ${data.transmission_method}, Parts: ${data.part_count}`);
      console.log(`   Used multipart: ${data.used_multipart}`);
    };
    
    httpClient.rpc.on("http_transmission_stats", onHttpTransmission);
    
    // Register a service that processes data
    const processData = (data) => {
      return { 
        processed: true, 
        size: data.byteLength || data.length, 
        checksum: (data.byteLength || data.length) % 1000000 
      };
    };
    
    const serviceInfo = await httpClient.registerService({
      id: "concurrent-service",
      name: "Concurrent Processing Service",
      config: { visibility: "public" },
      processData: processData
    });
    
    console.log(`✅ Service registered: ${serviceInfo.id}`);
    
    // Create multiple large datasets
    const datasets = [
      generateTestData(3),   // 3MB
      generateTestData(5),   // 5MB
      generateTestData(8),   // 8MB
      generateTestData(12),  // 12MB
    ];
    
    console.log(`📤 Sending ${datasets.length} concurrent transmissions...`);
    
    // Clear events
    httpTransmissionEvents.length = 0;
    
    // Send all datasets concurrently
    const startTime = Date.now();
    const service = await httpClient.getService("concurrent-service");
    const tasks = [];
    for (let i = 0; i < datasets.length; i++) {
      const task = service.processData(datasets[i]);
      tasks.push(task);
    }
    
    const results = await Promise.all(tasks);
    const totalTime = (Date.now() - startTime) / 1000;
    
    console.log(`✅ All ${datasets.length} transmissions completed in ${totalTime.toFixed(2)}s`);
    
    // Verify all results
    for (let i = 0; i < results.length; i++) {
      expect(results[i].processed).to.be.true;
      expect(results[i].size).to.equal(datasets[i].byteLength);
    }
    
    // Verify HTTP transmission events
    console.log(`📊 HTTP transmission events: ${httpTransmissionEvents.length}`);
    for (const event of httpTransmissionEvents) {
      console.log(`   - ${(event.content_length / (1024*1024)).toFixed(1)}MB: ${event.transmission_method} (${event.part_count} parts, multipart: ${event.used_multipart})`);
    }
    
    // All transmissions above 1MB should have used HTTP
    const expectedHttpTransmissions = datasets.filter(data => data.byteLength >= 1024 * 1024).length;
    expect(httpTransmissionEvents.length).to.equal(expectedHttpTransmissions);
    
    console.log("✅ Confirmed: Concurrent large transmissions work correctly");
  }).timeout(120000);

  it("should respect configurable HTTP transmission parameters", async function() {
    console.log("\n=== TESTING CONFIGURABLE HTTP TRANSMISSION PARAMETERS ===");
    
    // Verify S3 is available before testing HTTP transmission
    if (!httpClient.rpc._http_message_transmission_available) {
      console.warn("⚠️  S3/HTTP message transmission is not available - skipping test");
      this.skip();
      return;
    }
    
    // Create a separate client with custom thresholds
    const customHttpThreshold = 512 * 1024; // 512KB (lower than default 1MB)
    const customMultipartThreshold = 5 * 1024 * 1024; // 5MB (lower than default 10MB)
    
    const customClient = await connectToServer({
      name: "configurable-params-test",
      server_url: SERVER_URL,
      enable_http_transmission: true,
      http_transmission_threshold: customHttpThreshold,
      multipart_threshold: customMultipartThreshold,
    });
    
    // Track HTTP transmission events
    const httpTransmissionEvents = [];
    
    const onHttpTransmissionStats = (event) => {
      httpTransmissionEvents.push(event);
      console.log(`   📊 HTTP transmission event: ${(event.content_length / (1024*1024)).toFixed(1)}MB`);
      console.log(`      Method: ${event.transmission_method}, Parts: ${event.part_count}`);
      console.log(`      Used multipart: ${event.used_multipart}`);
      console.log(`      Part size: ${(event.part_size / (1024*1024)).toFixed(1)}MB`);
      console.log(`      Multipart threshold: ${(event.multipart_threshold / (1024*1024)).toFixed(1)}MB`);
    };
    
    customClient.rpc.on("http_transmission_stats", onHttpTransmissionStats);
    
    // Register a service that analyzes data
    const analyzeData = (data) => {
      return {
        size: data.byteLength || data.length,
        analyzed: true,
        checksum: (data.byteLength || data.length) % 1000000 // Simple checksum
      };
    };
    
    const serviceInfo = await customClient.registerService({
      id: "analyze-service",
      name: "Analyze Service",
      config: { visibility: "public" },
      analyzeData: analyzeData
    });
    
    console.log(`✅ Service registered: ${serviceInfo.id}`);
    
    // Get the service
    const service = await customClient.getService("analyze-service");
    
    // Test 1: Small message (should use HTTP single upload due to lowered threshold)
    console.log("📤 Testing small message (should use HTTP due to lowered threshold)...");
    const smallData = generateTestData(0.5); // 500KB
    const result1 = await service.analyzeData(smallData);
    expect(result1.size).to.equal(smallData.byteLength);
    expect(httpTransmissionEvents).to.have.length(1, "Small message should trigger HTTP transmission with lowered threshold");
    
    let event = httpTransmissionEvents[0];
    expect(event.transmission_method).to.equal("single_upload");
    expect(event.used_multipart).to.be.false;
    expect(event.part_count).to.equal(1);
    expect(event.multipart_threshold).to.equal(customMultipartThreshold);
    console.log("   ✅ Small message correctly used HTTP single upload (due to lowered threshold)");
    
    // Test 2: Medium message (should use HTTP single upload)
    console.log("📤 Testing medium message (should use HTTP single upload)...");
    const mediumData = generateTestData(3.0); // 3MB
    const result2 = await service.analyzeData(mediumData);
    expect(result2.size).to.equal(mediumData.byteLength);
    expect(httpTransmissionEvents).to.have.length(2, "Medium message should trigger HTTP transmission");
    
    event = httpTransmissionEvents[1];
    expect(event.transmission_method).to.equal("single_upload");
    expect(event.used_multipart).to.be.false;
    expect(event.part_count).to.equal(1);
    expect(event.multipart_threshold).to.equal(customMultipartThreshold);
    console.log("   ✅ Medium message correctly used HTTP single upload");
    
    // Test 3: Large message (should use HTTP multipart upload)
    console.log("📤 Testing large message (should use HTTP multipart upload)...");
    const largeData = generateTestData(8.0); // 8MB
    const result3 = await service.analyzeData(largeData);
    expect(result3.size).to.equal(largeData.byteLength);
    expect(httpTransmissionEvents).to.have.length(3, "Large message should trigger HTTP transmission");
    
    event = httpTransmissionEvents[2];
    expect(event.transmission_method).to.equal("multipart_upload");
    expect(event.used_multipart).to.be.true;
    expect(event.part_count).to.be.at.least(2); // 8MB / 6MB per part = at least 2 parts
    expect(event.multipart_threshold).to.equal(customMultipartThreshold);
    console.log("   ✅ Large message correctly used HTTP multipart upload");
    
    console.log("✅ All configurable parameter tests passed!");
    
    await customClient.disconnect();
  }).timeout(90000);

  it("should track actual multipart usage rather than computing from thresholds", async function() {
    console.log("\n=== TESTING TRACKING VERIFICATION ===");
    
    // Verify S3 is available before testing HTTP transmission
    if (!httpClient.rpc._http_message_transmission_available) {
      console.warn("⚠️  S3/HTTP message transmission is not available - skipping test");
      this.skip();
      return;
    }
    
    // Track HTTP transmission events
    const httpTransmissionEvents = [];
    
    const onHttpTransmission = (data) => {
      httpTransmissionEvents.push(data);
      console.log(`🔗 HTTP transmission event: ${(data.content_length / (1024*1024)).toFixed(1)}MB`);
      console.log(`   Method: ${data.transmission_method}, Parts: ${data.part_count}`);
      console.log(`   Used multipart: ${data.used_multipart}`);
      console.log(`   Part size: ${(data.part_size / (1024*1024)).toFixed(1)}MB`);
      console.log(`   Transmission time: ${data.transmission_time.toFixed(2)}s`);
    };
    
    httpClient.rpc.on("http_transmission_stats", onHttpTransmission);
    
    // Register a service that processes data
    const processData = (data) => {
      return { 
        processed: true, 
        size: data.byteLength || data.length,
        timestamp: Date.now()
      };
    };
    
    const serviceInfo = await httpClient.registerService({
      id: "tracking-service",
      name: "Tracking Service",
      config: { visibility: "public" },
      processData: processData
    });
    
    console.log(`✅ Service registered: ${serviceInfo.id}`);
    
    // Test with data that should trigger multipart upload
    const testData = generateTestData(25); // 25MB - should use multipart
    console.log(`📤 Sending ${(testData.byteLength / (1024*1024)).toFixed(1)}MB of data...`);
    
    const startTime = Date.now();
    const service = await httpClient.getService("tracking-service");
    const result = await service.processData(testData);
    const totalTime = (Date.now() - startTime) / 1000;
    
    console.log(`✅ Data processed successfully in ${totalTime.toFixed(2)}s`);
    expect(result.processed).to.be.true;
    expect(result.size).to.equal(testData.byteLength);
    
    // Verify tracking data
    expect(httpTransmissionEvents).to.have.length(1, "Should have one HTTP transmission event");
    const event = httpTransmissionEvents[0];
    
    // Verify that tracking shows actual usage, not computed values
    expect(event.content_length).to.equal(testData.byteLength);
    expect(event.transmission_method).to.equal("multipart_upload");
    expect(event.used_multipart).to.be.true;
    expect(event.part_count).to.be.at.least(5); // 25MB / 6MB per part = at least 5 parts
    expect(event.part_size).to.equal(6 * 1024 * 1024); // 6MB per part
    expect(event.transmission_time).to.be.a("number");
    expect(event.transmission_time).to.be.greaterThan(0);
    
    // Verify that part count is calculated based on actual data size, not threshold
    const expectedPartCount = Math.ceil(testData.byteLength / (6 * 1024 * 1024));
    expect(event.part_count).to.equal(expectedPartCount);
    
    console.log(`✅ Tracking verification passed:`);
    console.log(`   - Content length: ${event.content_length} bytes`);
    console.log(`   - Part count: ${event.part_count} (calculated: ${expectedPartCount})`);
    console.log(`   - Part size: ${event.part_size} bytes`);
    console.log(`   - Transmission time: ${event.transmission_time.toFixed(2)}s`);
  }).timeout(90000);

  it("should support configurable multipart size and parallel uploads", async function() {
    console.log("\n=== TESTING CONFIGURABLE MULTIPART SIZE AND PARALLEL UPLOADS ===");
    
    // Verify S3 is available before testing HTTP transmission
    if (!httpClient.rpc._http_message_transmission_available) {
      console.warn("⚠️  S3/HTTP message transmission is not available - skipping test");
      this.skip();
      return;
    }
    
    // Verify configuration was applied
    expect(httpClient.rpc._multipart_size).to.equal(10 * 1024 * 1024); // Default
    expect(httpClient.rpc._max_parallel_uploads).to.equal(5); // Default
    
    console.log(`✅ Multipart size configured: ${httpClient.rpc._multipart_size / (1024*1024)}MB`);
    console.log(`✅ Max parallel uploads configured: ${httpClient.rpc._max_parallel_uploads}`);
    
    // Track HTTP transmission events
    const httpTransmissionEvents = [];
    
    const onHttpTransmission = (data) => {
      httpTransmissionEvents.push(data);
      console.log(`🔗 HTTP transmission event: ${(data.content_length / (1024*1024)).toFixed(1)}MB`);
      console.log(`   - Multipart size: ${data.multipart_size / (1024*1024)}MB`);
      console.log(`   - Part count: ${data.part_count}`);
      console.log(`   - Used multipart: ${data.used_multipart}`);
    };
    
    httpClient.rpc.on("http_transmission_stats", onHttpTransmission);
    
    // Register a service that processes large data
    const processLargeData = (data) => {
      return {
        received_size: data.length || data.byteLength,
        processed: true,
        timestamp: Date.now()
      };
    };
    
    const serviceInfo = await httpClient.registerService({
      id: "large-data-processor",
      name: "Large Data Processor",
      description: "Processes large data with configurable multipart settings",
      config: {
        visibility: "public",
        require_context: false,
      },
      processLargeData: processLargeData,
    });
    
    console.log(`✅ Service registered: ${serviceInfo.id}`);
    
    // Test with data that will trigger multipart upload
    const testData = generateTestData(15); // 15MB - should trigger multipart with 6MB parts
    console.log(`📤 Sending ${(testData.byteLength / (1024*1024)).toFixed(1)}MB of test data`);
    
    const startTime = Date.now();
    const result = (await httpClient.getService("large-data-processor")).processLargeData(testData);
    const endTime = Date.now();
    
    console.log(`✅ Data processed successfully in ${(endTime - startTime) / 1000}s`);
    console.log(`✅ Result: ${JSON.stringify(result)}`);
    
    // Verify HTTP transmission was used
    expect(httpTransmissionEvents.length).to.be.greaterThan(0);
    
    const lastEvent = httpTransmissionEvents[httpTransmissionEvents.length - 1];
    expect(lastEvent.content_length).to.equal(testData.byteLength);
    expect(lastEvent.multipart_size).to.equal(10 * 1024 * 1024); // Should match our configuration
    expect(lastEvent.part_count).to.equal(Math.ceil(testData.byteLength / (10 * 1024 * 1024))); // Should be 2 parts for 15MB
    expect(lastEvent.used_multipart).to.be.true;
    
    console.log(`✅ Multipart configuration verified: ${lastEvent.part_count} parts of ${lastEvent.multipart_size / (1024*1024)}MB each`);
  }).timeout(60000);

  it("should demonstrate parallel upload performance benefits", async () => {
    console.log("\n=== TESTING PARALLEL UPLOAD PERFORMANCE ===");
    
    // Test with different parallel upload configurations
    const configs = [
      { max_parallel_uploads: 1, name: "Sequential" },
      { max_parallel_uploads: 3, name: "3 Parallel" },
      { max_parallel_uploads: 5, name: "5 Parallel" },
    ];
    
    for (const config of configs) {
      console.log(`\n--- Testing ${config.name} Uploads ---`);
      
      const client = await connectToServer({
        name: `parallel-test-${config.max_parallel_uploads}`,
        server_url: SERVER_URL,
        multipart_size: 2 * 1024 * 1024, // 2MB parts for more parts
        max_parallel_uploads: config.max_parallel_uploads,
      });
      
      try {
        const httpTransmissionEvents = [];
        
        const onHttpTransmission = (data) => {
          httpTransmissionEvents.push(data);
        };
        
        client.rpc.on("http_transmission_stats", onHttpTransmission);
        
        // Register echo service
        const echoData = (data) => data;
        
        await client.registerService({
          id: "echo-service",
          name: "Echo Service",
          config: { visibility: "public", require_context: false },
          echoData: echoData,
        });
        
        // Test with data that creates multiple parts
        const testData = generateTestData(12); // 12MB with 2MB parts = 6 parts
        console.log(`📤 Sending ${(testData.byteLength / (1024*1024)).toFixed(1)}MB with ${config.max_parallel_uploads} parallel uploads`);
        
        const startTime = Date.now();
        const result = (await client.getService("echo-service")).echoData(testData);
        const endTime = Date.now();
        
        const duration = (endTime - startTime) / 1000;
        console.log(`✅ Completed in ${duration.toFixed(2)}s`);
        
        expect(result).to.deep.equal(testData);
        expect(httpTransmissionEvents.length).to.be.greaterThan(0);
        
        const lastEvent = httpTransmissionEvents[httpTransmissionEvents.length - 1];
        expect(lastEvent.used_multipart).to.be.true;
        expect(lastEvent.part_count).to.equal(6); // 12MB / 2MB = 6 parts
        
        console.log(`   - Parts: ${lastEvent.part_count}`);
        console.log(`   - Part size: ${lastEvent.part_size / (1024*1024)}MB`);
        console.log(`   - Transmission time: ${lastEvent.transmission_time?.toFixed(2)}s`);
        
      } finally {
        await client.disconnect();
      }
    }
  }).timeout(120000);

}); 