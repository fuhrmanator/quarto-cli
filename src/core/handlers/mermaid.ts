/*
* mermaid.ts
*
* Copyright (C) 2022 by RStudio, PBC
*
*/

import {
  LanguageCellHandlerContext,
  LanguageCellHandlerOptions,
  LanguageHandler,
} from "./types.ts";
import { baseHandler, install } from "./base.ts";
import { formatResourcePath } from "../resources.ts";
import { join } from "path/mod.ts";
import {
  isJavascriptCompatible,
  isMarkdownOutput,
} from "../../config/format.ts";
import { QuartoMdCell } from "../lib/break-quarto-md.ts";
import {
  asMappedString,
  mappedConcat,
  MappedString,
} from "../lib/mapped-text.ts";

import {
  extractHtmlFromElements,
  extractImagesFromElements,
} from "../puppeteer.ts";
import { mappedStringFromFile } from "../mapped-text.ts";

const mermaidHandler: LanguageHandler = {
  ...baseHandler,

  type: "cell",
  stage: "post-engine",

  languageName: "mermaid",
  languageClass: (options: LanguageCellHandlerOptions) => {
    if (isMarkdownOutput(options.format.pandoc, ["gfm"])) {
      return "mermaid-source"; // sidestep github's in-band signaling of mermaid diagrams
    } else {
      return "default"; // no pandoc highlighting yet so we use 'default' to get grey shading
    }
  },

  defaultOptions: {
    echo: false,
    eval: true,
    include: true,
  },

  comment: "%%",

  async cell(
    handlerContext: LanguageCellHandlerContext,
    cell: QuartoMdCell,
    options: Record<string, unknown>,
  ) {
    const cellContent = handlerContext.cellContent(cell);
    // create puppeteer target page
    const tempDirName = handlerContext.options.temp.createDir();
    const content = `<html>
    <head>
    <script src="./mermaid.min.js"></script>
    </head>
    <body>
    <pre class="mermaid">\n${cellContent.value}\n</pre>
    <script>
    mermaid.initialize();
    </script>
    </html>`;
    const fileName = join(tempDirName, "index.html");
    Deno.writeTextFileSync(fileName, content);
    Deno.copyFileSync(
      formatResourcePath("html", join("mermaid", "mermaid.min.js")),
      join(tempDirName, "mermaid.min.js"),
    );
    const url = `file://${fileName}`;
    const selector = "pre.mermaid svg";

    if (isJavascriptCompatible(handlerContext.options.format)) {
      const svgText = (await extractHtmlFromElements(url, selector))[0];
      return this.build(handlerContext, cell, asMappedString(svgText), options);
    } else if (
      isMarkdownOutput(handlerContext.options.format.pandoc, ["gfm"])
    ) {
      return this.build(
        handlerContext,
        cell,
        mappedConcat(["\n``` mermaid\n", cellContent, "\n```\n"]),
        options,
      );
    } else {
      const { sourceName, fullName: tempName } = handlerContext
        .uniqueFigureName("dot-figure-", ".png");
      await extractImagesFromElements(url, selector, [tempName]);
      return this.build(
        handlerContext,
        cell,
        mappedConcat([`\n![](${sourceName}){fig-pos='H'}\n`]),
        options,
      );
    }
  },
};

install(mermaidHandler);
