# Cross-boundary request contracts

Each JSON file here is a **recording of a request body the frontend really
sends**, pinned from both sides:

- the frontend test asserts the body it produces deep-equals the file;
- the backend test feeds the same file to the real Pydantic model and
  asserts it validates.

Neither side can drift alone, and — the point — neither test can pass by
agreeing with itself. A frontend test that mocks the transport can only
prove "we sent the value we meant to send"; it cannot notice that the
server rejects that value. That is exactly how the asset-only turn shipped
broken: the panel test asserted `content === ''` reached a mocked
`streamChatMessage`, while the real endpoint answered 422 because
`ChatRequest.content` carried `min_length=1`.

| File | Path it pins |
|---|---|
| `chat-request-asset-only.json` | `POST /ai-library/sessions/{id}/chat-stream` for a turn carrying a library asset and no typed text |

Consumers:
- `frontend/services/aiLibraryService.contract.test.ts`
- `backend/tests/test_chat_request_content_or_attachments.py`

A missing file must FAIL the test, never skip it.
