const path = require("node:path");

globalThis.window = globalThis;
globalThis.self = globalThis;

const pageUrl = new URL("https://www.douyin.com/");
globalThis.location = {
    href: pageUrl.href,
    origin: pageUrl.origin,
    protocol: pageUrl.protocol,
    host: pageUrl.host,
    hostname: pageUrl.hostname,
    port: pageUrl.port,
    pathname: pageUrl.pathname,
    search: pageUrl.search,
    hash: pageUrl.hash,
};

class MemoryStorage {
    constructor() {
        this.values = new Map();
    }

    getItem(key) {
        return this.values.has(String(key)) ? this.values.get(String(key)) : null;
    }

    setItem(key, value) {
        this.values.set(String(key), String(value));
    }

    removeItem(key) {
        this.values.delete(String(key));
    }
}

globalThis.localStorage = new MemoryStorage();
globalThis.sessionStorage = new MemoryStorage();
globalThis.navigator = {
    userAgent: process.env.DOUYIN_UA || "",
    platform: "MacIntel",
    language: "en-US",
    languages: ["en-US", "en"],
    appName: "Netscape",
    appVersion: "5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    onLine: true,
    cookieEnabled: true,
    hardwareConcurrency: 12,
    deviceMemory: 32,
    sendBeacon() {
        return false;
    },
};
globalThis.screen = {
    width: 2560,
    height: 1440,
    availWidth: 2560,
    availHeight: 1415,
    colorDepth: 24,
    pixelDepth: 24,
};
globalThis.history = {length: 1, state: null};
globalThis.addEventListener = function () {};
globalThis.removeEventListener = function () {};

let cookieText = process.env.DOUYIN_COOKIE || "";
globalThis.document = {
    currentScript: {
        getAttribute(name) {
            if (name === "project-id") return "64";
            if (name === "custom-report-host") return "";
            return null;
        },
    },
    documentElement: {style: {}, clientWidth: 2560, clientHeight: 1440},
    body: {style: {}, appendChild() {}},
    domain: "www.douyin.com",
    referrer: "https://www.douyin.com/",
    createElement() {
        return {style: {}, setAttribute() {}, appendChild() {}};
    },
    getElementById() {
        return null;
    },
    addEventListener() {},
    removeEventListener() {},
    get cookie() {
        return cookieText;
    },
    set cookie(value) {
        cookieText = cookieText ? `${cookieText}; ${value}` : String(value);
    },
};

class XMLHttpRequest {
    constructor() {
        this._headers = {};
        this.upload = {addEventListener() {}};
    }

    open(method, url, async = true) {
        this._method = method;
        this._url = url;
        this._async = async;
    }

    setRequestHeader(name, value) {
        this._headers[name] = value;
    }

    send(body) {
        this._body = body;
    }

    addEventListener() {}
    removeEventListener() {}
    abort() {}
}

globalThis.XMLHttpRequest = XMLHttpRequest;
globalThis.window.SSR_RENDER_DATA = {app: {odin: {}}};
globalThis.window._secsdk_uifid = process.env.DOUYIN_UIFID || undefined;

require(path.join(__dirname, "websign_runtime.js"));

const inputUrl = process.argv[2];
if (!inputUrl) {
    process.stderr.write("missing URL\n");
    process.exit(2);
}
const signer = globalThis.window.use("webSignUrl");
if (typeof signer !== "function") {
    process.stderr.write("webSignUrl unavailable\n");
    process.exit(3);
}
const result = signer(inputUrl);
if (!result || typeof result.url !== "string") {
    process.stderr.write("webSignUrl returned no URL\n");
    process.exit(4);
}
process.stdout.write(`${result.url}\n`);
process.exit(0);
