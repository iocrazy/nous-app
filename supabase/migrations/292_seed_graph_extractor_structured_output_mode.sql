-- Add the graph extractor structured-output mode key to system_settings
-- (follow-up to 291). Controls how Graphiti's OpenAIGenericClient requests
-- structured output from the extractor LLM:
--   json_object  — default; schema injected into the prompt + simple
--                  response_format. Works on ModelScope/Qwen/DeepSeek-class
--                  providers that reject native json_schema constrained decoding
--                  (they return choices=None for Graphiti's complex schema).
--   json_schema  — native constrained decoding; opt-in, for providers that
--                  support it (real OpenAI).
--
-- Stored as a jsonb STRING via to_jsonb(...::text) like the other graph_* keys
-- so asyncpg deserializes it to a clean Python string. Default json_object.
-- The admin PUT endpoint validates the value against the allowed modes.

INSERT INTO system_settings (key, value, description)
VALUES
  ('graph_extractor_structured_output_mode', to_jsonb('json_object'::text),
   'Graphiti extractor structured-output mode (json_object | json_schema)')
ON CONFLICT (key) DO NOTHING;
