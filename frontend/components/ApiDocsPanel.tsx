
import React, { useState } from 'react';
import { Copy, Check, ChevronDown, ChevronRight, BookOpen, Shield, AlertTriangle, Server } from 'lucide-react';

const BASE_URL = 'https://cn.nous.ink:88';

// --- Helpers ---

const MethodBadge: React.FC<{ method: string }> = ({ method }) => {
  const colors: Record<string, string> = {
    GET: 'bg-blue-500/20 text-blue-400',
    POST: 'bg-green-500/20 text-green-400',
    PUT: 'bg-yellow-500/20 text-yellow-400',
    DELETE: 'bg-red-500/20 text-red-400',
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-mono font-semibold ${colors[method] || 'bg-ink-700 text-ink-300'}`}>
      {method}
    </span>
  );
};

const CopyButton: React.FC<{ text: string }> = ({ text }) => {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button
      onClick={handleCopy}
      className="p-1.5 rounded-md hover:bg-ink-700/50 text-ink-500 hover:text-ink-300 transition-colors"
      title="Copy"
    >
      {copied ? <Check size={14} className="text-green-400" /> : <Copy size={14} />}
    </button>
  );
};

const CodeBlock: React.FC<{ code: string; language?: string }> = ({ code, language = 'bash' }) => (
  <div className="relative group">
    <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
      <CopyButton text={code} />
    </div>
    <pre className="p-4 bg-ink-950 rounded-lg border border-ink-800/50 text-xs font-mono text-ink-300 overflow-x-auto whitespace-pre">
      <code>{code}</code>
    </pre>
  </div>
);

interface Param {
  name: string;
  type: string;
  required: boolean;
  default?: string;
  description: string;
}

const ParamsTable: React.FC<{ params: Param[] }> = ({ params }) => (
  <div className="overflow-x-auto">
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-ink-800">
          <th className="text-left py-2 px-3 text-ink-500 font-medium">Name</th>
          <th className="text-left py-2 px-3 text-ink-500 font-medium">Type</th>
          <th className="text-left py-2 px-3 text-ink-500 font-medium">Required</th>
          <th className="text-left py-2 px-3 text-ink-500 font-medium">Default</th>
          <th className="text-left py-2 px-3 text-ink-500 font-medium">Description</th>
        </tr>
      </thead>
      <tbody>
        {params.map((p) => (
          <tr key={p.name} className="border-b border-ink-800/50">
            <td className="py-2 px-3 font-mono text-[var(--accent-text)]">{p.name}</td>
            <td className="py-2 px-3 text-ink-400">{p.type}</td>
            <td className="py-2 px-3">
              {p.required
                ? <span className="text-amber-400">Yes</span>
                : <span className="text-ink-600">No</span>}
            </td>
            <td className="py-2 px-3 text-ink-500 font-mono">{p.default ?? '—'}</td>
            <td className="py-2 px-3 text-ink-400">{p.description}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

// --- Endpoint Data ---

interface EndpointDef {
  method: string;
  path: string;
  description: string;
  params?: Param[];
  queryParams?: Param[];
  curl: string;
  response: string;
}

const endpoints: EndpointDef[] = [
  {
    method: 'POST',
    path: '/api/v1/media/fetch',
    description: 'Parse and fetch a single video by share link. Returns parsed metadata and starts downloading media files.',
    params: [
      { name: 'url', type: 'string', required: true, description: 'Share link or video URL' },
      { name: 'video_bool', type: 'boolean', required: false, default: 'true', description: 'Download video file' },
      { name: 'cover_bool', type: 'boolean', required: false, default: 'true', description: 'Download cover image' },
      { name: 'use_celery', type: 'boolean', required: false, default: 'false', description: 'Process asynchronously via task queue' },
      { name: 'tag_ids', type: 'string[]', required: false, description: 'Existing tag UUIDs to attach' },
      { name: 'tags', type: 'string[]', required: false, description: 'Tag names to attach (auto-created if not found)' },
      { name: 'transcribe', type: 'boolean', required: false, default: 'false', description: 'Run AI transcription after download (maps to Pipeline system tags)' },
      { name: 'summarize', type: 'boolean', required: false, default: 'false', description: 'Run AI summary after download (maps to Pipeline system tags)' },
      { name: 'analyze', type: 'boolean', required: false, default: 'false', description: 'Run AI cover analysis after download (maps to Pipeline system tags)' },
      { name: 'rating', type: 'integer', required: false, description: 'Star rating written to the resource (0–5)' },
    ],
    curl: `curl -X POST "${BASE_URL}/api/v1/media/fetch" \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: your_api_key_here" \\
  -d '{
    "url": "https://v.douyin.com/xxxxxx/",
    "video_bool": true,
    "cover_bool": true,
    "tags": ["Music", "Tutorial"]
  }'`,
    response: `{
  "success": true,
  "message": "Video fetched successfully",
  "platform_id": "7312345678901234567",
  "title": "Video Title",
  "author": "Author Name",
  "media_type": 0,
  "like_count": 12345,
  "video_download_status": "completed"
}`,
  },
  {
    method: 'POST',
    path: '/api/v1/media/fetch/batch',
    description: 'Parse and fetch multiple videos at once. Returns results for each URL. AI processing (transcribe / summarize / analyze) needs use_celery: true; without it only tags and rating are applied.',
    params: [
      { name: 'urls', type: 'string[]', required: true, description: 'Array of share links' },
      { name: 'video_bool', type: 'boolean', required: false, default: 'true', description: 'Download video files' },
      { name: 'cover_bool', type: 'boolean', required: false, default: 'true', description: 'Download cover images' },
      { name: 'use_celery', type: 'boolean', required: false, default: 'false', description: 'Process asynchronously' },
      { name: 'tag_ids', type: 'string[]', required: false, description: 'Existing tag UUIDs to attach to all videos' },
      { name: 'tags', type: 'string[]', required: false, description: 'Tag names to attach (auto-created if not found)' },
      { name: 'transcribe', type: 'boolean', required: false, default: 'false', description: 'Run AI transcription after download (maps to Pipeline system tags)' },
      { name: 'summarize', type: 'boolean', required: false, default: 'false', description: 'Run AI summary after download (maps to Pipeline system tags)' },
      { name: 'analyze', type: 'boolean', required: false, default: 'false', description: 'Run AI cover analysis after download (maps to Pipeline system tags)' },
      { name: 'rating', type: 'integer', required: false, description: 'Star rating written to the resource (0–5)' },
    ],
    curl: `curl -X POST "${BASE_URL}/api/v1/media/fetch/batch" \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: your_api_key_here" \\
  -d '{
    "urls": [
      "https://v.douyin.com/aaaaaa/",
      "https://v.douyin.com/bbbbbb/"
    ],
    "video_bool": true,
    "tags": ["Dance"]
  }'`,
    response: `{
  "success": true,
  "count": 2,
  "videos": [ ... ],
  "errors": []
}`,
  },
  {
    method: 'GET',
    path: '/api/v1/media',
    description: 'List all videos in your library with pagination support.',
    queryParams: [
      { name: 'skip', type: 'integer', required: false, default: '0', description: 'Number of records to skip' },
      { name: 'limit', type: 'integer', required: false, default: '20', description: 'Max records to return (1–100)' },
      { name: 'order_by', type: 'string', required: false, default: 'created_at', description: 'Field to sort by' },
      { name: 'ascending', type: 'boolean', required: false, default: 'false', description: 'Sort ascending' },
    ],
    curl: `curl "${BASE_URL}/api/v1/media?limit=10&skip=0" \\
  -H "X-API-Key: your_api_key_here"`,
    response: `{
  "success": true,
  "count": 10,
  "videos": [
    {
      "platform_id": "7312345678901234567",
      "title": "Video Title",
      "author": "Author Name",
      "media_type": 0,
      "created_at": "2025-01-15T10:30:00Z"
    }
  ]
}`,
  },
  {
    method: 'GET',
    path: '/api/v1/media/{platform_id}',
    description: 'Get full details for a specific video by its platform ID.',
    curl: `curl "${BASE_URL}/api/v1/media/7312345678901234567" \\
  -H "X-API-Key: your_api_key_here"`,
    response: `{
  "success": true,
  "video": {
    "platform_id": "7312345678901234567",
    "title": "Video Title",
    "author": "Author Name",
    "description": "Video description...",
    "like_count": 12345,
    "comment_count": 678,
    "share_count": 90,
    "duration": "00:30",
    "published_at": "2025-01-15T10:30:00Z"
  }
}`,
  },
  {
    method: 'DELETE',
    path: '/api/v1/media/{platform_id}',
    description: 'Delete a video record and its associated media files.',
    curl: `curl -X DELETE "${BASE_URL}/api/v1/media/7312345678901234567" \\
  -H "X-API-Key: your_api_key_here"`,
    response: `{
  "success": true,
  "message": "Video deleted successfully"
}`,
  },
  {
    method: 'POST',
    path: '/api/v1/media/retry/{platform_id}',
    description: 'Retry downloading media for a video that previously failed.',
    curl: `curl -X POST "${BASE_URL}/api/v1/media/retry/7312345678901234567" \\
  -H "X-API-Key: your_api_key_here"`,
    response: `{
  "success": true,
  "message": "Retry initiated"
}`,
  },
  {
    method: 'GET',
    path: '/api/v1/media/statistics',
    description: 'Get aggregate statistics for your video library.',
    curl: `curl "${BASE_URL}/api/v1/media/statistics" \\
  -H "X-API-Key: your_api_key_here"`,
    response: `{
  "success": true,
  "statistics": {
    "total_count": 150,
    "status_distribution": { "completed": 140, "failed": 5, "pending": 5 },
    "type_distribution": { "video": 120, "image": 30 }
  }
}`,
  },
  {
    method: 'POST',
    path: '/api/v1/media/search',
    description: 'Search videos by keyword, author, status, date range, and more.',
    params: [
      { name: 'keyword', type: 'string', required: false, description: 'Search in title and description' },
      { name: 'author', type: 'string', required: false, description: 'Filter by author name' },
      { name: 'status', type: 'string', required: false, description: 'Filter by download status' },
      { name: 'media_type', type: 'string', required: false, description: 'Filter by media type' },
      { name: 'category', type: 'string', required: false, description: 'Filter by category tag' },
      { name: 'start_date', type: 'ISO datetime', required: false, description: 'Filter from date' },
      { name: 'end_date', type: 'ISO datetime', required: false, description: 'Filter to date' },
    ],
    queryParams: [
      { name: 'skip', type: 'integer', required: false, default: '0', description: 'Records to skip' },
      { name: 'limit', type: 'integer', required: false, default: '20', description: 'Max records (1–100)' },
    ],
    curl: `curl -X POST "${BASE_URL}/api/v1/media/search?limit=10" \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: your_api_key_here" \\
  -d '{
    "keyword": "dance",
    "author": "creator_name"
  }'`,
    response: `{
  "success": true,
  "count": 3,
  "videos": [ ... ]
}`,
  },
  {
    method: 'POST',
    path: '/api/v1/inspiration/notes',
    description: 'Write a note into the inspiration library from an external script. Requires an API key with the "Inspiration Notes" scope. Note: this endpoint authenticates via the Authorization: Bearer header (not X-API-Key).',
    params: [
      { name: 'content_md', type: 'string', required: true, description: 'Note body in Markdown (#hashtags become tags)' },
    ],
    curl: `curl -X POST "${BASE_URL}/api/v1/inspiration/notes" \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer dk_your_api_key_here" \\
  -d '{
    "content_md": "a captured idea #inbox"
  }'`,
    response: `{
  "id": "123456789",
  "content_md": "a captured idea #inbox",
  "tags": ["inbox"],
  "note_date": "2026-07-18",
  "pinned": false,
  "attachments": [],
  "created_at": "2026-07-18T00:00:00Z",
  "updated_at": "2026-07-18T00:00:00Z"
}`,
  },
];

// --- Collapsible Endpoint ---

const EndpointSection: React.FC<{ ep: EndpointDef }> = ({ ep }) => {
  const [open, setOpen] = useState(false);

  return (
    <div className="border border-ink-800 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-ink-800/30 transition-colors text-left"
      >
        {open ? <ChevronDown size={16} className="text-ink-500 flex-shrink-0" /> : <ChevronRight size={16} className="text-ink-500 flex-shrink-0" />}
        <MethodBadge method={ep.method} />
        <code className="text-sm font-mono text-ink-300 flex-1">{ep.path}</code>
        <span className="text-xs text-ink-500 hidden sm:inline">{ep.description.split('.')[0]}</span>
      </button>

      {open && (
        <div className="border-t border-ink-800 p-4 space-y-4 bg-ink-900/30 animate-in fade-in duration-200">
          <p className="text-sm text-ink-400">{ep.description}</p>

          {ep.params && ep.params.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-ink-500 uppercase tracking-wider mb-2">Request Body (JSON)</h4>
              <ParamsTable params={ep.params} />
            </div>
          )}

          {ep.queryParams && ep.queryParams.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-ink-500 uppercase tracking-wider mb-2">Query Parameters</h4>
              <ParamsTable params={ep.queryParams} />
            </div>
          )}

          <div>
            <h4 className="text-xs font-semibold text-ink-500 uppercase tracking-wider mb-2">Example Request</h4>
            <CodeBlock code={ep.curl} />
          </div>

          <div>
            <h4 className="text-xs font-semibold text-ink-500 uppercase tracking-wider mb-2">Example Response</h4>
            <CodeBlock code={ep.response} language="json" />
          </div>
        </div>
      )}
    </div>
  );
};

// --- Scopes Table ---

const scopesData = [
  { scope: 'videos:fetch', description: 'Parse single media link' },
  { scope: 'videos:fetch:batch', description: 'Batch parse multiple links' },
  { scope: 'videos:videos:read', description: 'List and get media details' },
  { scope: 'videos:videos:write', description: 'Create and update media' },
  { scope: 'videos:search', description: 'Search media' },
  { scope: 'videos:statistics', description: 'View statistics' },
  { scope: 'videos:retry', description: 'Retry failed downloads' },
  { scope: 'tags:read', description: 'List tags' },
  { scope: 'tags:write', description: 'Create and update tags' },
  { scope: 'tags:delete', description: 'Delete tags' },
  { scope: 'collections:read', description: 'List collections' },
  { scope: 'collections:write', description: 'Create and update collections' },
  { scope: 'collections:delete', description: 'Delete collections' },
  { scope: 'system:read', description: 'View system status' },
  { scope: 'inspiration:write', description: 'Write notes into the inspiration library' },
];

// --- Error Codes Table ---

const errorCodes = [
  { code: '400', status: 'Bad Request', description: 'Invalid request body or parameters' },
  { code: '401', status: 'Unauthorized', description: 'Missing or invalid API key / token' },
  { code: '403', status: 'Forbidden', description: 'Insufficient permissions or scope' },
  { code: '404', status: 'Not Found', description: 'Resource does not exist' },
  { code: '429', status: 'Too Many Requests', description: 'Rate limit exceeded' },
  { code: '500', status: 'Internal Server Error', description: 'Unexpected server error' },
];

// --- Main Component ---

export const ApiDocsPanel: React.FC = () => {
  return (
    <div className="space-y-8 max-w-4xl mx-auto animate-in fade-in duration-300">

      {/* Header */}
      <div className="flex items-center gap-4">
        <div className="p-3 bg-[var(--accent-soft)] rounded-xl">
          <BookOpen size={24} className="text-[var(--accent-text)]" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-ink-50">API Documentation</h1>
          <p className="text-ink-400 text-sm">Integrate with Nous programmatically</p>
        </div>
      </div>

      {/* Section 1: Quick Start */}
      <section className="bg-ink-900 border border-ink-800 rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-ink-800 bg-ink-900/50">
          <h2 className="font-semibold text-ink-200 flex items-center gap-2">
            <Server size={18} className="text-[var(--accent-text)]" />
            Quick Start
          </h2>
        </div>
        <div className="p-6 space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="p-4 bg-ink-950/50 rounded-lg border border-ink-800/50">
              <div className="text-xs text-ink-500 mb-1">Base URL</div>
              <code className="text-sm text-[var(--accent-text)] font-mono break-all">{BASE_URL}</code>
            </div>
            <div className="p-4 bg-ink-950/50 rounded-lg border border-ink-800/50">
              <div className="text-xs text-ink-500 mb-1">Authentication</div>
              <code className="text-sm text-[var(--accent-text)] font-mono">X-API-Key</code>
              <span className="text-ink-500 text-xs ml-1">header</span>
            </div>
            <div className="p-4 bg-ink-950/50 rounded-lg border border-ink-800/50">
              <div className="text-xs text-ink-500 mb-1">Content Type</div>
              <code className="text-sm text-[var(--accent-text)] font-mono">application/json</code>
            </div>
          </div>

          <div>
            <h3 className="text-sm font-medium text-ink-300 mb-2">Try it now</h3>
            <CodeBlock code={`curl -X POST "${BASE_URL}/api/v1/media/fetch" \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: your_api_key_here" \\
  -d '{"url": "https://v.douyin.com/xxxxxx/", "video_bool": true}'`} />
          </div>
        </div>
      </section>

      {/* Section 2: Authentication */}
      <section className="bg-ink-900 border border-ink-800 rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-ink-800 bg-ink-900/50">
          <h2 className="font-semibold text-ink-200 flex items-center gap-2">
            <Shield size={18} className="text-[var(--accent-text)]" />
            Authentication
          </h2>
        </div>
        <div className="p-6 space-y-5">
          <p className="text-sm text-ink-400">
            Two authentication methods are supported. Use <strong className="text-ink-200">API Key</strong> for server-to-server integrations, or <strong className="text-ink-200">Bearer Token</strong> for user sessions.
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-ink-300">Method 1: API Key (Recommended)</h3>
              <p className="text-xs text-ink-500">Generate keys in Settings &gt; API Management</p>
              <CodeBlock code={`# Include in request header
X-API-Key: mhk_xxxxxxxxxxxxxxxx`} />
            </div>
            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-ink-300">Method 2: Bearer Token</h3>
              <p className="text-xs text-ink-500">Obtained from /auth/signin response</p>
              <CodeBlock code={`# Include in request header
Authorization: Bearer eyJhbGciOi...`} />
            </div>
          </div>

          {/* Common Mistakes */}
          <div className="p-4 bg-amber-500/5 rounded-lg border border-amber-500/20">
            <div className="flex items-start gap-3">
              <AlertTriangle size={16} className="text-amber-400 mt-0.5 flex-shrink-0" />
              <div className="text-xs text-warn space-y-1">
                <p className="font-medium text-warn">Common Mistakes</p>
                <ul className="list-disc list-inside space-y-0.5">
                  <li>Putting API key in query params (<code className="text-amber-400/70">?api_key=xxx</code>) — use <code className="text-amber-400/70">X-API-Key</code> header instead</li>
                  <li>Sending form-data — use <code className="text-amber-400/70">Content-Type: application/json</code> with a JSON body</li>
                  <li>Missing the <code className="text-amber-400/70">/api/v1</code> prefix — all endpoints require it</li>
                </ul>
              </div>
            </div>
          </div>

          {/* Scopes */}
          <div>
            <h3 className="text-sm font-semibold text-ink-300 mb-3">Available Scopes</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-ink-800">
                    <th className="text-left py-2 px-3 text-ink-500 font-medium">Scope</th>
                    <th className="text-left py-2 px-3 text-ink-500 font-medium">Description</th>
                  </tr>
                </thead>
                <tbody>
                  {scopesData.map((s) => (
                    <tr key={s.scope} className="border-b border-ink-800/50">
                      <td className="py-2 px-3 font-mono text-[var(--accent-text)]">{s.scope}</td>
                      <td className="py-2 px-3 text-ink-400">{s.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </section>

      {/* Section 3: Endpoints */}
      <section className="bg-ink-900 border border-ink-800 rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-ink-800 bg-ink-900/50">
          <h2 className="font-semibold text-ink-200">Endpoints</h2>
          <p className="text-xs text-ink-500 mt-0.5">Click an endpoint to expand details, parameters, and examples</p>
        </div>
        <div className="p-4 space-y-2">
          {endpoints.map((ep) => (
            <EndpointSection key={`${ep.method}-${ep.path}`} ep={ep} />
          ))}
        </div>
      </section>

      {/* Section 4: Error Codes */}
      <section className="bg-ink-900 border border-ink-800 rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-ink-800 bg-ink-900/50">
          <h2 className="font-semibold text-ink-200 flex items-center gap-2">
            <AlertTriangle size={18} className="text-amber-400" />
            Error Codes
          </h2>
        </div>
        <div className="p-6">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-ink-800">
                  <th className="text-left py-2 px-3 text-ink-500 font-medium text-xs">Code</th>
                  <th className="text-left py-2 px-3 text-ink-500 font-medium text-xs">Status</th>
                  <th className="text-left py-2 px-3 text-ink-500 font-medium text-xs">Description</th>
                </tr>
              </thead>
              <tbody>
                {errorCodes.map((e) => (
                  <tr key={e.code} className="border-b border-ink-800/50">
                    <td className="py-2 px-3 font-mono text-amber-400 text-xs">{e.code}</td>
                    <td className="py-2 px-3 text-ink-300 text-xs">{e.status}</td>
                    <td className="py-2 px-3 text-ink-400 text-xs">{e.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-4">
            <h3 className="text-xs font-semibold text-ink-500 uppercase tracking-wider mb-2">Error Response Format</h3>
            <CodeBlock code={`{
  "detail": "Error message describing what went wrong"
}`} language="json" />
          </div>
        </div>
      </section>
    </div>
  );
};
