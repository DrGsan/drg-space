import {expect} from '@playwright/test';

// Check the final CSS cascade, rather than just the highlighter's color values.
export async function assertCodeTheme(page){
  const blocks=page.locator('.bn-editor .bn-block-content[data-content-type="codeBlock"]');
  await expect(blocks.first()).toBeVisible();
  await expect.poll(()=>blocks.locator('code span[style*="--shiki-light"]').count()).toBeGreaterThan(1);
  const measured=await blocks.evaluateAll(elements=>{
    const luminance=color=>{
      const values=color.match(/[\d.]+/g).slice(0,3).map(Number).map(value=>value/255);
      return values.map(value=>value<=.04045?value/12.92:((value+.055)/1.055)**2.4)
        .reduce((total,value,index)=>total+value*[.2126,.7152,.0722][index],0);
    };
    return elements.map(block=>{
      const surface=getComputedStyle(block).backgroundColor,bg=luminance(surface);
      const tokens=[...block.querySelectorAll('code span[style*="--shiki-light"]')].filter(token=>token.textContent.trim());
      const colors=tokens.map(token=>getComputedStyle(token).color);
      const contrasts=colors.map(color=>{const ink=luminance(color);return (Math.max(ink,bg)+.05)/(Math.min(ink,bg)+.05);});
      const code=block.querySelector('code');
      return {surface,backgroundLuminance:bg,minContrast:Math.min(...contrasts),colors:[...new Set(colors)],codeBackground:getComputedStyle(code).backgroundColor};
    });
  });
  const dark=await page.locator('html').getAttribute('data-scheme')==='dark';
  for(const block of measured){
    expect(block.codeBackground).toBe('rgba(0, 0, 0, 0)');
    if(dark)expect(block.backgroundLuminance).toBeLessThan(.1);
    else expect(block.backgroundLuminance).toBeGreaterThan(.8);
    expect(block.minContrast,JSON.stringify(block)).toBeGreaterThanOrEqual(4.5);
    expect(block.colors.length).toBeGreaterThan(1);
  }
}
