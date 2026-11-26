# Third-party notices

Memory OS AI is distributed under `LGPL-3.0-or-later` (see `LICENSE` and `LICENSE.GPL-3.0`). The wheel also ships
the third-party data file below, under its own licence. The package licence expression is therefore
`LGPL-3.0-or-later AND MIT`.

## `cl100k_base.tiktoken` (OpenAI tiktoken), MIT

- Shipped as `memory_os_v4/resources/cl100k_base.tiktoken`: the `cl100k_base` byte-pair-encoding ranks, used to
  count the tokens of the compact brief exactly and offline.
- Source: `https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken`, published by OpenAI for the
  `tiktoken` library (`https://github.com/openai/tiktoken`).
- SHA-256: `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`, 1,681,126 bytes. It is verified each
  time the table is loaded.
- The table file has no licence header of its own. It is distributed by OpenAI as data of `tiktoken`, whose licence
  follows. The regular expression `PATTERN` in `memory_os_v4/brief_tokens.py` also comes from `tiktoken`
  (`tiktoken_ext/openai_public.py`).
- `MEMORY_V4_CL100K_PATH` can still point to another copy of the same table. Its SHA-256 must then be identical.

```text
MIT License

Copyright (c) 2022 OpenAI, Shantanu Jain

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
