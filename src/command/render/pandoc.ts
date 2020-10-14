import { basename, dirname } from "path/mod.ts";
import { stringify } from "encoding/yaml.ts";

import type { FormatPandoc } from "../../api/format.ts";
import { execProcess, ProcessResult } from "../../core/process.ts";
import { consoleWriteLine } from "../../core/console.ts";

export interface PandocOptions {
  input: string;
  format: FormatPandoc;
  args: string[];
  quiet?: boolean;
}

export async function runPandoc(
  options: PandocOptions,
): Promise<ProcessResult> {
  // build the pandoc command
  const cmd = ["pandoc", basename(options.input)];

  // write a temporary default file from the options
  const yaml = "---\n" + stringify(options.format);
  const yamlFile = await Deno.makeTempFile(
    { prefix: "quarto-defaults", suffix: ".yml" },
  );
  await Deno.writeTextFile(yamlFile, yaml);
  cmd.push("--defaults", yamlFile);

  // add user command line args
  cmd.push(...[
    "--citeproc",
    ...options.args,
  ]);

  // print defaults file and command line args
  if (!options.quiet) {
    if (options.args.length > 0) {
      consoleWriteLine(yaml + "args: " + options.args.join(" "));
    } else {
      consoleWriteLine(yaml);
    }
    consoleWriteLine("---\n");
  }

  // run pandoc
  return await execProcess({
    cmd,
    cwd: dirname(options.input),
  });
}
