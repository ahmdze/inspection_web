const DB='InspDB', STORE='pending'; let db;

// تهيئة قاعدة البيانات
function initDB(){
    return new Promise((res,rej)=>{
        const r=indexedDB.open(DB, 2);
        r.onupgradeneeded=e=>{
            db=e.target.result;
            if(!db.objectStoreNames.contains(STORE)){
                db.createObjectStore(STORE,{keyPath:'id',autoIncrement:true})
            }
        };
        r.onsuccess=e=>{db=e.target.result;res()};
        r.onerror=e=>rej(e)
    })
}

// حفظ بيانات في IndexedDB
function save(d){
    return new Promise((res,rej)=>{
        if(!db){
            rej(new Error('قاعدة البيانات غير مهيأة'));
            return;
        }
        const t=db.transaction(STORE,'readwrite');
        t.objectStore(STORE).add(d);
        t.oncomplete=()=>{res()};
        t.onerror=rej
    })
}

// جلب جميع البيانات المعلقة
function getPending(){
    return new Promise((res,rej)=>{
        if(!db){
            res([]);
            return;
        }
        const t=db.transaction(STORE,'readonly');
        const r=t.objectStore(STORE).getAll();
        r.onsuccess=()=>res(r.result||[]);
        r.onerror=rej
    })
}

// حذف عنصر بعد الرفع الناجح
function del(id){
    return new Promise((res,rej)=>{
        if(!db){
            rej(new Error('قاعدة البيانات غير مهيأة'));
            return;
        }
        const t=db.transaction(STORE,'readwrite');
        t.objectStore(STORE).delete(id);
        t.oncomplete=res;
        t.onerror=rej
    })
}

// مزامنة تقارير محددة يدوياً
async function syncSelected(ids){
    if(!navigator.onLine){
        alert('⚠️ لا يوجد اتصال بالإنترنت! تأكد من الاتصال أولاً.');
        return false;
    }

    if(!ids || ids.length === 0){
        alert('الرجاء تحديد تقرير واحد على الأقل');
        return false;
    }

    const all = await getPending();
    const toSync = all.filter(item => ids.includes(item.id));

    if(toSync.length === 0){
        alert('لا توجد تقارير محددة للمزامنة');
        return false;
    }

    let successCount = 0;
    let failCount = 0;

    for(const item of toSync){
        try{
            console.log('[SYNC] جاري رفع العنصر ID:', item.id);

            const fd = new FormData();
            for(const key in item){
                if(key !== 'id' && item[key] !== null && item[key] !== undefined){
                    fd.append(key, String(item[key]));
                }
            }

            const r = await fetch(`/inspect/submit`, {
                method: 'POST',
                body: fd,
                credentials: 'include'
            });

            if(r.ok || r.status === 302){
                await del(item.id);
                successCount++;
                console.log('[SYNC] تم رفع العنصر بنجاح ID:', item.id);
            } else {
                failCount++;
                console.error('[SYNC] فشل الرفع ID:', item.id, 'status:', r.status);
            }
        } catch(e){
            failCount++;
            console.error('[SYNC] خطأ في الرفع ID:', item.id, e);
        }
    }

    renderOfflineList();

    if(successCount > 0){
        upd(`✅ تم رفع ${successCount} تقارير بنجاح`);
    }
    if(failCount > 0){
        setTimeout(()=>alert(`⚠️ فشل رفع ${failCount} تقارير. حاول مرة أخرى.`), 500);
    }

    return successCount > 0;
}

// عرض قائمة التقارير المعلقة
async function renderOfflineList(){
    const container = document.getElementById('offline-reports-list');
    const emptyMsg = document.getElementById('empty-offline-msg');
    const actions = document.getElementById('offline-actions');
    const countBadge = document.getElementById('pending-count');

    if(!container) return;

    const items = await getPending();

    if(countBadge){
        countBadge.textContent = items.length;
        if(items.length > 0){
            countBadge.style.display = 'inline-block';
        } else {
            countBadge.style.display = 'none';
        }
    }

    if(items.length === 0){
        container.innerHTML = '';
        if(emptyMsg) emptyMsg.style.display = 'block';
        if(actions) actions.style.display = 'none';
        return;
    }

    if(emptyMsg) emptyMsg.style.display = 'none';
    if(actions) actions.style.display = 'block';

    container.innerHTML = '';

    items.forEach(item => {
        const date = new Date(item.timestamp || item.created_at || Date.now()).toLocaleString('ar-SA');
        const location = item.location_name || item.site_id || 'غير محدد';
        const inspector = item.inspector_name || 'مفتش';

        const card = document.createElement('div');
        card.className = 'report-card';
        card.innerHTML = `
            <div class="report-info">
                <input type="checkbox" class="report-checkbox" value="${item.id}">
                <div>
                    <strong>تقرير #${item.id}</strong>
                    <div class="meta">${date} - ${location}</div>
                    <div class="sub-meta">${inspector}</div>
                </div>
            </div>
        `;
        container.appendChild(card);
    });

    setupSelectAll();
}

function setupSelectAll(){
    const selectAll = document.getElementById('select-all');
    const checkboxes = document.querySelectorAll('.report-checkbox');

    if(selectAll && checkboxes.length > 0){
        selectAll.addEventListener('change', (e) => {
            checkboxes.forEach(cb => cb.checked = e.target.checked);
        });
    }
}

// التعامل مع زر المزامنة اليدوية
async function handleManualSync(){
    const checkboxes = document.querySelectorAll('.report-checkbox:checked');
    const statusDiv = document.getElementById('sync-status');

    if(checkboxes.length === 0){
        alert('الرجاء تحديد تقرير واحد على الأقل');
        return;
    }

    const ids = Array.from(checkboxes).map(cb => parseInt(cb.value));

    if(statusDiv){
        statusDiv.textContent = 'جاري الرفع...';
        statusDiv.className = 'status syncing';
        statusDiv.classList.remove('hidden');
    }

    await syncSelected(ids);

    if(statusDiv){
        setTimeout(()=>{
            statusDiv.classList.add('hidden');
        }, 3000);
    }
}

// تحديث حالة المزامنة في الواجهة
function upd(m){
    const e=document.getElementById('sync-status');
    if(e){
        e.textContent=m;
        e.classList.remove('hidden');
        setTimeout(()=>e.classList.add('hidden'), 5000);
    }
}

// دالة التعامل مع الإرسال عند عدم الاتصال
window.handleOfflineSubmit = async function(form, code){
    const btn=form.querySelector('button[type=submit]');
    const originalText = btn.textContent;
    btn.disabled=true;
    btn.textContent='جاري الحفظ...';

    const formData = new FormData(form);
    const data = {};

    for(const [key, value] of formData.entries()){
        if(key !== 'code'){
            data[key] = value;
        }
    }

    try{
        await initDB();
        await save(data);
        upd('💾 تم الحفظ محلياً');
        alert('✅ تم حفظ البيانات محلياً بنجاح!\n\nانتقل إلى صفحة \"التقارير المعلقة\" لرفعها عند العودة للاتصال.');
        window.location.href='/inspect/success';
    }catch(e){
        console.error('[OFFLINE] فشل الحفظ:', e);
        localStorage.setItem('pending_submission_' + Date.now(), JSON.stringify(data));
        upd('💾 تم الحفظ محلياً (localStorage)');
        alert('✅ تم حفظ البيانات محلياً بنجاح!');
        window.location.href='/inspect/success';
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

// التهيئة عند تحميل الصفحة
document.addEventListener('DOMContentLoaded', async ()=>{
    try{
        await initDB();
        console.log('[INIT] IndexedDB initialized');

        if(document.getElementById('offline-reports-list')){
            renderOfflineList();

            const syncBtn = document.getElementById('sync-selected-btn');
            if(syncBtn){
                syncBtn.addEventListener('click', handleManualSync);
            }
        }

        upd(navigator.onLine?'🌐 متصل':'📴 أوفلاين - يحفظ محلياً');

        window.addEventListener('online',()=>{
            console.log('[EVENT] الاتصال استُعيد');
            upd('🌐 عاد الاتصال - يمكنك رفع التقارير الآن');
        });

        window.addEventListener('offline',()=>{
            console.log('[EVENT] انقطع الاتصال');
            upd('📴 انقطع - يحفظ محلياً')
        });

    }catch(e){
        console.error('[INIT] فشل تهيئة IndexedDB:', e);
    }
});