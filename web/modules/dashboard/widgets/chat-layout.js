// Reserve complete rows only; the overview never pages or scrolls its rooms.
export function chatWidgetGeometry(size,width,height,scale=1){
  const compact=size==='1x1',gap=8,rowHeight=(compact?84:size==='2x2'?88:68)*scale;
  const columns=compact?Math.max(1,Math.min(3,Math.floor((width+gap)/(76*scale+gap)))):size.startsWith('2')&&width>=520*scale?2:1;
  return {columns,rowHeight,capacity:columns*Math.max(0,Math.floor((height+gap)/(rowHeight+gap)))};
}
