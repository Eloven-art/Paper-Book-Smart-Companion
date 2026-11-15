// 本机书库保存整本素材；预览模式使用，真实后端仍可替换这一层。
const DB_NAME = 'paper-book-library-v1';
let dbPromise, pdfModule;
const pdfDocuments = new Map(), pageRenders = new Map();
const failure = message => Object.assign(new Error(message), { code: 'LOCAL_LIBRARY_ERROR' });
function database() {
  if (!globalThis.indexedDB) return Promise.reject(failure('当前浏览器不允许保存本机书库。请允许网站存储，或换普通浏览器窗口；素材尚未保存。'));
  if (!dbPromise) dbPromise = new Promise((resolve,reject)=>{
    const request=indexedDB.open(DB_NAME,1);
    request.onupgradeneeded=()=>{if(!request.result.objectStoreNames.contains('books'))request.result.createObjectStore('books',{keyPath:'book_id'});};
    request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(failure('无法打开本机书库，请检查网站存储权限。'));
    request.onblocked=()=>reject(failure('书库正在另一个页面升级，请关闭旧页面后再试。'));
  }).catch(error=>{dbPromise=null;throw error;});
  return dbPromise;
}
async function transaction(mode,operation) {
  const db=await database();return new Promise((resolve,reject)=>{
    const tx=db.transaction('books',mode),request=operation(tx.objectStore('books'));let result;
    request.onsuccess=()=>result=request.result;
    tx.oncomplete=()=>resolve(result);tx.onerror=()=>reject(failure('本机书库未能保存。可能是存储空间不足，原有书籍仍保留。'));tx.onabort=()=>reject(failure('本机保存被中断，导入尚未完成。'));
  });
}
export async function saveLocalBook(book) {
  if (!book.local_upload) return;
  const record=structuredClone(book);
  for(const page of record.pages)if(page.image_blob||record.pdf_blob)delete page.page_image_url;
  for(const item of record.supporting_files||[])delete item.url;
  await transaction('readwrite',store=>store.put(record));book.persistent=true;
}
export async function loadLocalBooks() {
  const records=await transaction('readonly',store=>store.getAll());
  return records.map(book=>{for(const page of book.pages)if(page.image_blob)page.page_image_url=URL.createObjectURL(page.image_blob);for(const item of book.supporting_files||[])if(item.file)item.url=URL.createObjectURL(item.file);book.persistent=true;return book;});
}
async function pdfjs() {
  if(!pdfModule){pdfModule=await import('../vendor/pdf.mjs');pdfModule.GlobalWorkerOptions.workerSrc=new URL('../vendor/pdf.worker.mjs',import.meta.url).href;}
  return pdfModule;
}
async function openPdf(book) {
  if(!pdfDocuments.has(book.book_id)) pdfDocuments.set(book.book_id,(async()=>{
    const engine=await pdfjs();const resourceUrl=new URL('../assets/pdf-resources/',import.meta.url),resourceBase=resourceUrl.protocol==='file:'?decodeURIComponent(resourceUrl.pathname):resourceUrl.href;return engine.getDocument({data:new Uint8Array(await book.pdf_blob.arrayBuffer()),isEvalSupported:false,standardFontDataUrl:resourceBase+'standard_fonts/',cMapUrl:resourceBase+'cmaps/',cMapPacked:true}).promise;
  })().catch(error=>{pdfDocuments.delete(book.book_id);throw Object.assign(failure('PDF 无法打开，可能已加密、受损或浏览器不支持。请换一份未加密文件。'),{cause:error});}));
  return pdfDocuments.get(book.book_id);
}
function progress(message){globalThis.dispatchEvent?.(new CustomEvent('pbsc-import-progress',{detail:{message}}));}
export async function importLocalBook(files,title,signal) {
  signal?.throwIfAborted();await database();
  const pdfs=files.filter(f=>f.type==='application/pdf'||/\.pdf$/i.test(f.name));
  if(pdfs.length&&(files.length!==1||pdfs.length!==1))throw failure('整本 PDF 一次选一本；页面图片可以一次多选。');
  const id='local-'+crypto.randomUUID().slice(0,12);
  const book={book_id:id,title:title||files[0]?.name.replace(/\.[^.]+$/,'')||'我的绘本',pages:[],video:null,local_upload:true,imported_at:new Date().toISOString()};
  try{
    if(pdfs.length){
      book.pdf_blob=pdfs[0];book.source_kind='pdf';const pdf=await openPdf(book);
      if(pdf.numPages>300)throw failure('这份 PDF 超过 300 页，请先按绘本分开导入。');
      for(let index=1;index<=pdf.numPages;index++){
        signal?.throwIfAborted();progress(`正在整理整本绘本：第 ${index} / ${pdf.numPages} 页`);
        const page=await pdf.getPage(index),viewport=page.getViewport({scale:1}),content=await page.getTextContent();
        const engine=await pdfjs(),blocks=[];let lines=[],line='';
        for(const item of content.items){if(!('str'in item)||!item.str)continue;const t=engine.Util.transform(viewport.transform,item.transform),height=Math.max(1,Math.hypot(t[2],t[3]));blocks.push({text:item.str,bbox:[t[4],t[5]-height,t[4]+item.width,t[5]]});line+=item.str;if(item.hasEOL){lines.push(line);line='';}}
        if(line)lines.push(line);
        book.pages.push({page_no:index,image_size:{width:viewport.width,height:viewport.height},ocr_text:{full_text:lines.join('\n'),blocks},narration:'',objects:[],scene_summary:'',plot_events:[],source_reference:{original_filename:pdfs[0].name,pdf_page:index,text_source:blocks.length?'pdf_text_layer':'image_only'}});
        page.cleanup();
      }
    }else{
      book.source_kind='images';
      for(const file of files){
        signal?.throwIfAborted();progress(`正在保存页面：${book.pages.length+1} / ${files.length}`);
        if(!['image/png','image/jpeg','image/webp'].includes(file.type))throw failure('页面图片请使用 JPG、PNG 或 WebP。');
        const url=URL.createObjectURL(file),image=new Image();image.src=url;
        try{await image.decode();}catch{URL.revokeObjectURL(url);throw failure(`读不出图片 ${file.name}。`);}
        book.pages.push({page_no:book.pages.length+1,page_image_url:url,image_blob:file,image_size:{width:image.naturalWidth,height:image.naturalHeight},ocr_text:{full_text:'',blocks:[]},narration:'',objects:[],scene_summary:'',plot_events:[],source_reference:{original_filename:file.name,text_source:'image_only'}});
      }
    }
    signal?.throwIfAborted();progress('正在保存到本机书架……');await saveLocalBook(book);return book;
  }catch(error){book.pages.forEach(p=>{if(p.page_image_url?.startsWith('blob:'))URL.revokeObjectURL(p.page_image_url)});const pdf=await pdfDocuments.get(id)?.catch(()=>null);pdf?.destroy();pdfDocuments.delete(id);throw error;}
}
export async function renderLocalPage(book,pageNo) {
  const page=book.pages.find(p=>p.page_no===pageNo);if(!page)throw failure('这一页不存在。');
  if(page.page_image_url)return page;
  if(!book.pdf_blob)return page;
  const key=book.book_id+':'+pageNo;
  if(!pageRenders.has(key))pageRenders.set(key,(async()=>{
    const pdf=await openPdf(book),source=await pdf.getPage(pageNo),original=source.getViewport({scale:1}),scale=Math.min(2,1800/Math.max(original.width,original.height)),viewport=source.getViewport({scale});
    const canvas=document.createElement('canvas');canvas.width=Math.ceil(viewport.width);canvas.height=Math.ceil(viewport.height);
    await source.render({canvasContext:canvas.getContext('2d'),viewport}).promise;
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',0.9));if(!blob)throw failure('当前页面未能渲染，请重新打开绘本。');
    page.page_image_url=URL.createObjectURL(blob);canvas.width=1;canvas.height=1;source.cleanup();return page;
  })().finally(()=>pageRenders.delete(key)));
  return pageRenders.get(key);
}
