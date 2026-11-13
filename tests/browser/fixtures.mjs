import {readFile} from 'node:fs/promises';

// The production build keeps aliases in its manifest, not as duplicate files.
export async function readAppStyles() {
  const root=new URL('../../app/static/',import.meta.url);
  const manifest=JSON.parse(await readFile(new URL('asset-manifest.json',root),'utf8'));
  return readFile(new URL('assets/'+manifest.aliases['app.css'],root),'utf8');
}

// Cookie-backed locale initialization needs an HTTP document, not about:blank.
export async function openTestOrigin(page) {
  if(/^https?:\/\//.test(page.url()))return;
  const origin='http://fixture.test';
  await page.route(origin+'/',route=>route.fulfill({contentType:'text/html',body:'<!doctype html>'}));
  await page.route(origin+'/assets/**',async route=>{
    try {
      const path=new URL(route.request().url()).pathname.slice('/assets/'.length);
      await route.fulfill({body:await readFile(new URL('../../app/static/assets/'+path,import.meta.url))});
    } catch {await route.abort();}
  });
  await page.goto(origin+'/');
}
