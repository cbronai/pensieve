import * as esbuild from 'esbuild';
import { parseArgs } from 'node:util';

const { values } = parseArgs({
  options: { watch: { type: 'boolean', default: false } },
  strict: false,
});

const buildOptions = {
  entryPoints: ['src/worker.ts'],
  bundle: true,
  outfile: 'dist/worker.js',
  format: 'esm',
  platform: 'node',
  target: 'node20',
  sourcemap: true,
  external: ['@paperclipai/plugin-sdk'],
};

if (values.watch) {
  const ctx = await esbuild.context(buildOptions);
  await ctx.watch();
  console.log('Watching...');
} else {
  await esbuild.build(buildOptions);
  console.log('Build complete → dist/worker.js');
}
