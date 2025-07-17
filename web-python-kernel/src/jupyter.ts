// TypeScript implementation of the Jupyter functionality for Deno
// Based on the original jupyter.js from Deno's core

// Symbol used for Jupyter display
export const $display = Symbol.for("Jupyter.display");

// HTML entity escaping for safe display
const rawToEntityEntries: Array<[string, string]> = [
  ["&", "&amp;"],
  ["<", "&lt;"],
  [">", "&gt;"],
  ['"', "&quot;"],
  ["'", "&#39;"],
];

const rawToEntity = new Map(rawToEntityEntries);
const rawRe = new RegExp(`[${[...rawToEntity.keys()].join("")}]`, "g");

export function escapeHTML(str: string): string {
  return str.replaceAll(
    rawRe,
    (m) => rawToEntity.has(m) ? rawToEntity.get(m)! : m,
  );
}

// Type definitions for various objects
export interface MediaBundle {
  [mimeType: string]: any;
}

// Type guard functions for various visualization libraries

// Vega-like objects
export function isVegaLike(obj: any): boolean {
  return obj !== null && typeof obj === "object" && "toSpec" in obj;
}

export function extractVega(obj: any): MediaBundle | null {
  const spec = obj.toSpec();
  if (!("$schema" in spec)) {
    return null;
  }
  if (typeof spec !== "object") {
    return null;
  }
  let mediaType = "application/vnd.vega.v5+json";
  if (spec.$schema === "https://vega.github.io/schema/vega-lite/v4.json") {
    mediaType = "application/vnd.vegalite.v4+json";
  } else if (
    spec.$schema === "https://vega.github.io/schema/vega-lite/v5.json"
  ) {
    mediaType = "application/vnd.vegalite.v5+json";
  }
  return {
    [mediaType]: spec,
  };
}

// DataFrame-like objects (Polars)
export function isDataFrameLike(obj: any): boolean {
  const isObject = obj !== null && typeof obj === "object";
  if (!isObject) {
    return false;
  }
  const df = obj;
  return df.schema !== undefined && typeof df.schema === "object" &&
    df.head !== undefined && typeof df.head === "function" &&
    df.toRecords !== undefined && typeof df.toRecords === "function";
}

export function mapPolarsTypeToJSONSchema(colType: any): string {
  const typeMapping: Record<string, string> = {
    Null: "null",
    Bool: "boolean",
    Int8: "integer",
    Int16: "integer",
    Int32: "integer",
    Int64: "integer",
    UInt8: "integer",
    UInt16: "integer",
    UInt32: "integer",
    UInt64: "integer",
    Float32: "number",
    Float64: "number",
    Date: "string",
    Datetime: "string",
    Utf8: "string",
    Categorical: "string",
    List: "array",
    Struct: "object",
  };
  // These colTypes are weird. When you console.dir or console.log them
  // they show a `DataType` field, however you can't access it directly until you
  // convert it to JSON
  const dataType = colType.toJSON()["DataType"];
  return typeMapping[dataType] || "string";
}

export function extractDataFrame(df: any): MediaBundle {
  const fields: Array<{name: string, type: string}> = [];
  const schema = {
    fields,
  };
  let data: any[] = [];
  
  // Convert DataFrame schema to Tabular DataResource schema
  for (const [colName, colType] of Object.entries(df.schema)) {
    const dataType = mapPolarsTypeToJSONSchema(colType);
    schema.fields.push({
      name: colName as string,
      type: dataType,
    });
  }
  
  // Convert DataFrame data to row-oriented JSON
  data = df.head(50).toRecords();
  
  let htmlTable = "<table>";
  htmlTable += "<thead><tr>";
  schema.fields.forEach((field) => {
    htmlTable += `<th>${escapeHTML(String(field.name))}</th>`;
  });
  htmlTable += "</tr></thead>";
  htmlTable += "<tbody>";
  df.head(10).toRecords().forEach((row: any) => {
    htmlTable += "<tr>";
    schema.fields.forEach((field) => {
      htmlTable += `<td>${escapeHTML(String(row[field.name]))}</td>`;
    });
    htmlTable += "</tr>";
  });
  htmlTable += "</tbody></table>";
  
  return {
    "application/vnd.dataresource+json": { data, schema },
    "text/html": htmlTable,
  };
}

// Canvas-like objects
export function isCanvasLike(obj: any): boolean {
  return obj !== null && typeof obj === "object" && "toDataURL" in obj;
}

// Image format detection
export function isJpg(obj: unknown): obj is Uint8Array {
  // Check if obj is a Uint8Array
  if (!(obj instanceof Uint8Array)) {
    return false;
  }

  // JPG files start with the magic bytes FF D8
  if (obj.length < 2 || obj[0] !== 0xFF || obj[1] !== 0xD8) {
    return false;
  }

  // JPG files end with the magic bytes FF D9
  if (
    obj.length < 2 || obj[obj.length - 2] !== 0xFF ||
    obj[obj.length - 1] !== 0xD9
  ) {
    return false;
  }

  return true;
}

export function isPng(obj: unknown): obj is Uint8Array {
  // Check if obj is a Uint8Array
  if (!(obj instanceof Uint8Array)) {
    return false;
  }

  // PNG files start with a specific 8-byte signature
  const pngSignature = [137, 80, 78, 71, 13, 10, 26, 10];

  // Check if the array is at least as long as the signature
  if (obj.length < pngSignature.length) {
    return false;
  }

  // Check each byte of the signature
  for (let i = 0; i < pngSignature.length; i++) {
    if (obj[i] !== pngSignature[i]) {
      return false;
    }
  }

  return true;
}

// HTML and SVG element detection
export function isSVGElementLike(obj: any): boolean {
  return obj !== null && typeof obj === "object" && "outerHTML" in obj &&
    typeof obj.outerHTML === "string" && obj.outerHTML.startsWith("<svg");
}

export function isHTMLElementLike(obj: any): boolean {
  return obj !== null && typeof obj === "object" && "outerHTML" in obj &&
    typeof obj.outerHTML === "string";
}

// Check if object has display symbol
export function hasDisplaySymbol(obj: any): boolean {
  return obj !== null && typeof obj === "object" && $display in obj &&
    typeof obj[$display] === "function";
}

export function makeDisplayable(obj: MediaBundle): any {
  return {
    [$display]: () => obj,
  };
}

/**
 * Format an object for displaying in Deno
 *
 * @param obj - The object to be displayed
 * @returns MediaBundle
 */
export async function format(obj: any): Promise<MediaBundle> {
  if (hasDisplaySymbol(obj)) {
    return await obj[$display]();
  }
  
  if (typeof obj !== "object") {
    return {
      "text/plain": String(obj),
    };
  }

  if (isCanvasLike(obj)) {
    const dataURL = obj.toDataURL();
    const parts = dataURL.split(",");
    const mime = parts[0].split(":")[1].split(";")[0];
    const data = parts[1];
    return {
      [mime]: data,
    };
  }
  
  if (isVegaLike(obj)) {
    return extractVega(obj) || { "text/plain": "Invalid Vega specification" };
  }
  
  if (isDataFrameLike(obj)) {
    return extractDataFrame(obj);
  }
  
  if (isJpg(obj)) {
    return {
      "image/jpeg": convertUint8ArrayToBase64(obj),
    };
  }
  
  if (isPng(obj)) {
    return {
      "image/png": convertUint8ArrayToBase64(obj),
    };
  }
  
  if (isSVGElementLike(obj)) {
    return {
      "image/svg+xml": obj.outerHTML,
    };
  }
  
  if (isHTMLElementLike(obj)) {
    return {
      "text/html": obj.outerHTML,
    };
  }
  
  // Fallback to plain text representation
  return {
    "text/plain": JSON.stringify(obj, null, 2),
  };
}

// Helper function to convert Uint8Array to base64
function convertUint8ArrayToBase64(data: Uint8Array): string {
  return btoa(String.fromCharCode(...data));
}

/**
 * This function creates a tagged template function for a given media type.
 * The tagged template function takes a template string and returns a displayable object.
 *
 * @param mediatype - The media type for the tagged template function.
 * @returns A function that takes a template string and returns a displayable object.
 */
function createTaggedTemplateDisplayable(mediatype: string): Function {
  return (strings: TemplateStringsArray, ...values: any[]) => {
    const payload = strings.reduce(
      (acc, string, i) =>
        acc + string + (values[i] !== undefined ? String(values[i]) : ""),
      "",
    );
    return makeDisplayable({ [mediatype]: payload });
  };
}

/**
 * Show Markdown in Jupyter frontends with a tagged template function.
 */
export const md = createTaggedTemplateDisplayable("text/markdown");

/**
 * Show HTML in Jupyter frontends with a tagged template function.
 */
export const html = createTaggedTemplateDisplayable("text/html");

/**
 * SVG Tagged Template Function.
 */
export const svg = createTaggedTemplateDisplayable("image/svg+xml");

export function image(obj: string | Uint8Array): any {
  if (typeof obj === "string") {
    throw new Error("File path strings not supported in browser environment. Please use Uint8Array directly.");
  }

  if (isJpg(obj)) {
    return makeDisplayable({ "image/jpeg": convertUint8ArrayToBase64(obj) });
  }

  if (isPng(obj)) {
    return makeDisplayable({ "image/png": convertUint8ArrayToBase64(obj) });
  }

  throw new TypeError(
    "Object is not a valid image or a path to an image. `Deno.jupyter.image` supports displaying JPG or PNG images.",
  );
}

export function isMediaBundle(obj: any): obj is MediaBundle {
  if (obj == null || typeof obj !== "object" || Array.isArray(obj)) {
    return false;
  }
  for (const key in obj) {
    if (typeof key !== "string") {
      return false;
    }
  }
  return true;
}

export async function formatInner(obj: any, raw: boolean): Promise<MediaBundle> {
  if (raw && isMediaBundle(obj)) {
    return obj;
  } else {
    return await format(obj);
  }
}

interface DisplayOptions {
  raw?: boolean;
  update?: boolean;
  display_id?: string;
}

// Empty EventEmitter class for now - we'll implement our own event handling
export class EventEmitter {
  private events: Record<string, Array<(...args: any[]) => void>> = {};

  public on(eventName: string, listener: (...args: any[]) => void): this {
    if (!this.events[eventName]) {
      this.events[eventName] = [];
    }
    this.events[eventName].push(listener);
    return this;
  }

  public emit(eventName: string, ...args: any[]): boolean {
    if (!this.events[eventName]) {
      return false;
    }
    this.events[eventName].forEach(listener => listener(...args));
    return true;
  }

  public removeListener(eventName: string, listener: (...args: any[]) => void): this {
    if (!this.events[eventName]) {
      return this;
    }
    this.events[eventName] = this.events[eventName].filter(l => l !== listener);
    return this;
  }
}

// Jupyter functionality namespace
export class JupyterNamespace {
  public $display = $display;
  public display = this._display.bind(this);
  public format = format;
  public md = md;
  public html = html;
  public svg = svg;
  public image = image;
  
  private _eventEmitter = new EventEmitter();

  constructor() {}

  /**
   * Format a result value for display in the TypeScript kernel
   * @param result The value to format
   * @returns A formatted object suitable for Jupyter display
   */
  public formatResult(result: any): any {
    // Default display is plain text (without colors for consistent testing)
    const output: any = {
      "text/plain": typeof result === 'object' ? JSON.stringify(result, null, 2) : String(result)
    };
    
    // Try to detect special types for richer display
    if (result !== null && typeof result === "object") {
      // Check for various rich display types
      if (hasDisplaySymbol(result)) {
        try {
          const displayResult = result[$display]();
          if (displayResult && typeof displayResult === "object") {
            Object.assign(output, displayResult);
          }
        } catch (e) {
          console.error("Error in display symbol execution:", e);
        }
      } else if (isVegaLike(result)) {
        try {
          const vegaOutput = extractVega(result);
          if (vegaOutput) {
            Object.assign(output, vegaOutput);
          }
        } catch (e) {
          console.error("Error extracting Vega data:", e);
        }
      } else if (isDataFrameLike(result)) {
        try {
          const dfOutput = extractDataFrame(result);
          Object.assign(output, dfOutput);
        } catch (e) {
          console.error("Error extracting DataFrame data:", e);
        }
      } else if (isCanvasLike(result)) {
        try {
          const dataURL = result.toDataURL();
          const parts = dataURL.split(",");
          const mime = parts[0].split(":")[1].split(";")[0];
          const data = parts[1];
          output[mime] = data;
        } catch (e) {
          console.error("Error extracting Canvas data:", e);
        }
      } else if (isHTMLElementLike(result)) {
        try {
          output["text/html"] = result.outerHTML;
        } catch (e) {
          console.error("Error extracting HTML data:", e);
        }
      }
    }
    
    return output;
  }

  private async _display(obj: any, options: DisplayOptions = { raw: false, update: false }): Promise<void> {
    const bundle = await formatInner(obj, options.raw || false);
    let messageType = "display_data";
    if (options.update) {
      messageType = "update_display_data";
    }
    let transient: Record<string, any> = {};
    if (options.display_id) {
      transient = { display_id: options.display_id };
    }
    
    await this.broadcast(messageType, {
      data: bundle,
      metadata: {},
      transient,
    });
    
    return;
  }

  public async broadcast(msgType: string, content: any, options: { metadata?: Record<string, any>, buffers?: any[] } = {}): Promise<void> {
    const metadata = options.metadata || {};
    const buffers = options.buffers || [];
    
    // Emit an event that our kernel can listen for
    this._eventEmitter.emit('broadcast', msgType, content, metadata, buffers);
  }

  // Allow subscriptions to broadcast events
  public onBroadcast(callback: (msgType: string, content: any, metadata: Record<string, any>, buffers: any[]) => void): void {
    this._eventEmitter.on('broadcast', callback);
  }

  // Remove broadcast subscription
  public offBroadcast(callback: (msgType: string, content: any, metadata: Record<string, any>, buffers: any[]) => void): void {
    this._eventEmitter.removeListener('broadcast', callback);
  }
}

// Create and export the Jupyter namespace
export const jupyter = new JupyterNamespace();

// Browser environment - no global namespace attachment needed 