import { defineConfig } from 'astro/config';
import react from '@astrojs/react';
import tailwindcss from '@tailwindcss/vite';

// On GitHub Pages the site lives at https://puppyum.github.io/rote/, so
// the workflow sets BASE=/rote/. Cloudflare Pages serves the site at
// the root, so BASE is unset there.
const base = process.env.BASE ?? '/';
const site = process.env.SITE ?? 'https://rote-companion.pages.dev';

export default defineConfig({
  site,
  base,
  trailingSlash: 'ignore',
  integrations: [react()],
  vite: {
    plugins: [tailwindcss()],
  },
  build: {
    inlineStylesheets: 'always',
  },
});
