# Query rewriting examples (v1, not yet benchmarked)

## 1. Chinese request, add an English query

User: 找几段手持跟拍的镜头

Calls:
- `LibrarySearch(query="手持跟拍")`
- `LibrarySearch(query="handheld tracking shot")`

Merge by `resource_id`; a hit found by both calls is a stronger candidate.

## 2. Long sentence, split into sub-queries

User: 我之前存过一个日本街头夜景、下着雨、霓虹灯倒映在地面的视频，帮我找出来

Calls:
- `LibrarySearch(query="日本 夜景 雨")`
- `LibrarySearch(query="霓虹 倒影")`
- `LibrarySearch(query="rainy Tokyo street at night neon reflections")`

## 3. Exact phrase, keyword layer only

User: find the video called "Morning Routine 2024"

Calls:
- `LibrarySearch(query="Morning Routine 2024", layers=["text"])`

If nothing comes back, retry once without `layers` before telling the user it
is not in the library.
