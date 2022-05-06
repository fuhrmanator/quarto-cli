/*
* extension-shared.ts
*
* Copyright (C) 2020 by RStudio, PBC
*
*/
import { Metadata } from "../config/types.ts";

export const kContributes = "contributes";
export const kCommon = "common";
export const kExtensionDir = "_extensions";

export const kTitle = "title";
export const kAuthor = "author";
export const kVersion = "version";

export interface Extension extends Record<string, unknown> {
  id: ExtensionId;
  title: string;
  author: string;
  version?: ExtensionVersion;
  path: string;
  [kContributes]: {
    shortcodes?: string[];
    filters?: string[];
    format?: Record<string, unknown>;
  };
}

export interface ExtensionId {
  name: string;
  organization?: string;
}

export interface ExtensionVersion {
  major: number;
  minor: number;
  revision: number;
  build: number;
}

export interface ExtensionMetadata {
  path: string;
  metadata: Metadata;
}
