# Query rewriting examples (v1, not yet benchmarked)

How the text layer matches: the whole query is one substring of a title,
description or tag. Spaces next to Chinese characters are dropped ("日本 夜景"
is searched as "日本夜景"); spaces between Latin words are kept. So send one
keyword or one exact phrase per call, and leave combinations to the semantic
layer.

## 1. Chinese request, add an English query

User: 找几段手持跟拍的镜头

Calls:
- `LibrarySearch(query="跟拍")`
- `LibrarySearch(query="handheld tracking shot")`

Merge by `resource_id`; a hit found by both calls is a stronger candidate.

## 2. Long sentence, split into sub-queries

User: 我之前存过一个日本街头夜景、下着雨、霓虹灯倒映在地面的视频，帮我找出来

Calls:
- `LibrarySearch(query="夜景")`
- `LibrarySearch(query="霓虹")`
- `LibrarySearch(query="rainy Tokyo street at night with neon reflections")`

The two short keywords hit titles directly; the English sentence finds the
same scene by meaning when no title says it.

## 3. Exact phrase, keyword layer only

User: find the video called "Morning Routine 2024"

Calls:
- `LibrarySearch(query="Morning Routine", layers=["text"])`

Keep the distinctive words and drop what the saved title may spell
differently (the year in brackets, a trailing emoji). If nothing comes back,
retry once without `layers` before telling the user it is not in the library.
