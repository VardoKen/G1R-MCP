# Third-party notices

G1R MCP's original code is licensed under the MIT License in [LICENSE](LICENSE).
Third-party components retain their own copyrights and licenses.

## Components incorporated into the native DLL

### JSON for Modern C++ (nlohmann/json)

Version: **3.12.0**, commit
[`55f93686c01528224f448c19128836e7df245f72`](https://github.com/nlohmann/json/tree/55f93686c01528224f448c19128836e7df245f72).
This header-only JSON library is used by the native transport and reflection
code. `native/CMakeLists.txt` fetches tag `v3.12.0`.

[Upstream license](https://github.com/nlohmann/json/blob/55f93686c01528224f448c19128836e7df245f72/LICENSE.MIT):

```text
MIT License

Copyright (c) 2013-2025 Niels Lohmann

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

Additional copyright notices in the headers included by this version:

- Copyright (c) 2008-2009 Bjoern Hoehrmann <bjoern@hoehrmann.de>:
  [UTF-8 decoder](https://github.com/nlohmann/json/blob/55f93686c01528224f448c19128836e7df245f72/include/nlohmann/detail/output/serializer.hpp), MIT.
- Copyright (c) 2009 Florian Loitsch:
  [Grisu2 number formatting](https://github.com/nlohmann/json/blob/55f93686c01528224f448c19128836e7df245f72/include/nlohmann/detail/conversions/to_chars.hpp), MIT.
- Copyright (c) 2016-2021 Evan Nemerson <evan@nemerson.com>:
  [adapted Hedley macros](https://github.com/nlohmann/json/blob/55f93686c01528224f448c19128836e7df245f72/include/nlohmann/thirdparty/hedley/hedley.hpp), MIT according to the distributed header's SPDX identifier.
  The [upstream JSON README](https://github.com/nlohmann/json/blob/55f93686c01528224f448c19128836e7df245f72/README.md#license)
  identifies the original Hedley project as
  [CC0-1.0](https://creativecommons.org/publicdomain/zero/1.0/).

The MIT permission and disclaimer above apply to the MIT portions listed here.

The JSON headers also attribute their C++11 integer-sequence fallback to
Copyright 2018 The Abseil Authors, under
[Apache-2.0](https://github.com/nlohmann/json/blob/55f93686c01528224f448c19128836e7df245f72/LICENSES/Apache-2.0.txt).
That code is inside the pre-C++14 branch of
[`cpp_future.hpp`](https://github.com/nlohmann/json/blob/55f93686c01528224f448c19128836e7df245f72/include/nlohmann/detail/meta/cpp_future.hpp).
G1R MCP requires C++20 and uses the standard-library branch, so that fallback
is not compiled into its native DLL.

### MinHook and Hacker Disassembler Engine

Revision:
[`8af6b4acae5a9388fd742b56fa79ece89d96f823`](https://github.com/TsudaKageyu/minhook/tree/8af6b4acae5a9388fd742b56fa79ece89d96f823).
MinHook is used for native hooks. Its default static build incorporates Hacker
Disassembler Engine 64 C on the required Windows x64 target.

The following is the complete upstream BSD-2-Clause license file, including
its HDE32 and HDE64 notices:
[LICENSE.txt](https://github.com/TsudaKageyu/minhook/blob/8af6b4acae5a9388fd742b56fa79ece89d96f823/LICENSE.txt).

```text
MinHook - The Minimalistic API Hooking Library for x64/x86
Copyright (C) 2009-2017 Tsuda Kageyu.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

 1. Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.
 2. Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in the
    documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER
OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

================================================================================
Portions of this software are Copyright (c) 2008-2009, Vyacheslav Patkov.
================================================================================
Hacker Disassembler Engine 32 C
Copyright (c) 2008-2009, Vyacheslav Patkov.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

 1. Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.
 2. Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in the
    documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE REGENTS OR
CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

-------------------------------------------------------------------------------
Hacker Disassembler Engine 64 C
Copyright (c) 2008-2009, Vyacheslav Patkov.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

 1. Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.
 2. Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in the
    documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE REGENTS OR
CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## Separately installed Python dependencies

The Python server directly depends on **mcp 2.3.0** (Model Context Protocol SDK).
That package depends on **mcp-types 2.3.0**, which supplies protocol wire types.
Both installed distributions declare **MIT** in their package metadata and
include a license headed `Copyright (c) 2024 Anthropic, PBC`.

- [MCP Python SDK repository](https://github.com/modelcontextprotocol/python-sdk)
- [mcp 2.3.0 on PyPI](https://pypi.org/project/mcp/2.3.0/)
- [mcp-types 2.3.0 on PyPI](https://pypi.org/project/mcp-types/2.3.0/)

These packages and their transitive dependencies are installed separately by
`uv` or `pip`; they are not vendored into G1R MCP's wheel or native DLL.
Their own license files accompany their installed distributions. The exact
resolved dependency versions are recorded in `uv.lock`.

## AngelScript debugger protocol reference

The debugger codec is an independent implementation of the wire format
described by Hazelight's
[Unreal AngelScript VS Code extension](https://github.com/Hazelight/vscode-unreal-angelscript/tree/ffc7ab2948589abb04200b7dcedd326eaeaf7a4d),
revision `ffc7ab2948589abb04200b7dcedd326eaeaf7a4d`.
The reference project is MIT licensed, Copyright 2019 Hazelight Games AB.
Its source code is not copied into the codec.

## External software and data

The Gothic 1 Remake game, Unreal Engine, official G1R Modkit, Python runtime,
and Microsoft Visual C++ runtime are external requirements. They are not
included in this repository's G1R MCP distribution and are not relicensed by
G1R MCP's MIT License. Game data, official scripts, and third-party mod sources
accessed by the tool remain subject to their respective terms.
