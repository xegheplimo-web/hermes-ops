#!/usr/bin/env node
/**
 * `hermes-policy-gate` bin entry. Thin wrapper around the pure {@link runCli}
 * logic with the real Node filesystem and stdio.
 */

import { readFileSync, statSync, writeFileSync } from 'node:fs';
import { runCli, type CliIo } from './cli.js';

const io: CliIo = {
  stdout: process.stdout,
  stderr: process.stderr,
  readFileSync,
  writeFileSync,
  statSync,
};

process.exit(runCli(process.argv.slice(2), io));
