/*
* graphviz.ts
*
* Copyright (C) 2022 by RStudio, PBC
*
*/

import { LanguageCellHandlerContext, LanguageHandler } from "./types.ts";
import { graphviz } from "../graphviz/graphviz-wasm.js";
import { baseHandler, install } from "./base.ts";
import { formatResourcePath } from "../resources.ts";
import { join } from "path/mod.ts";
import {
  isJavascriptCompatible,
  isMarkdownOutput,
} from "../../config/format.ts";
import { QuartoMdCell } from "../lib/break-quarto-md.ts";
import { asMappedString, mappedConcat } from "../lib/mapped-text.ts";

import { extractImagesFromElements } from "../puppeteer.ts";

let globalFigureCounter = 0;

const dotHandler: LanguageHandler = {
  ...baseHandler,

  type: "cell",
  stage: "post-engine",

  languageName: "dot",

  defaultOptions: {
    echo: false,
    eval: true,
    include: true,
  },

  comment: "//",

  async cell(
    handlerContext: LanguageCellHandlerContext,
    cell: QuartoMdCell,
    options: Record<string, unknown>,
  ) {
    console.log(cell.source.value);
    const svg = await graphviz().layout(cell.source.value, "svg", "dot");
    console.log(svg);

    if (isJavascriptCompatible(handlerContext.options.format)) {
      return this.build(
        handlerContext,
        cell,
        mappedConcat(["```{=html}\n", svg, "```"]),
        options,
      );
    } else if (
      isMarkdownOutput(handlerContext.options.format.pandoc, ["gfm"])
    ) {
      return this.build(
        handlerContext,
        cell,
        mappedConcat(["\n``` dot\n", cell.source, "\n```\n"]),
        options,
      );
    } else {
      // create puppeteer target page
      const dirName = handlerContext.options.temp.createDir();
      const content = `<!DOCTYPE html><html><body>${svg}</body></html>`;
      const fileName = join(dirName, "index.html");
      Deno.writeTextFileSync(fileName, content);
      const url = `file://${fileName}`;
      const selector = "svg";

      const pngName = `dot-figure-${++globalFigureCounter}.png`;
      const tempName = join(dirName, pngName);
      await extractImagesFromElements(url, selector, [tempName]);
      return this.build(
        handlerContext,
        cell,
        mappedConcat([`\n![](${tempName})\n`]),
        options,
      );
    }
  },
};

install(dotHandler);
