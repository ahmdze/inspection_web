const DB='InspDB', STORE='pending'; let db;

// تهيئة قاعدة البيانات
function initDB(){
    return new Promise((res,rej)=>{
        const r=indexedDB.open(DB,1);
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

// مزامنة البيانات مع السيرفر
async function sync(){
    console.log('[SYNC] sync() called - navigator.onLine:', navigator.onLine);
    
    if(!navigator.onLine){
        console.log('[SYNC] لا يوجد اتصال - تأجيل المزامنة');
        return false;
    }
    
    // التأكد من تهيئة قاعدة البيانات
    if(!db){
        console.log('[SYNC] قاعدة البيانات غير مهيأة - جاري التهيئة...');
        try {
            await initDB();
        } catch(e) {
            console.error('[SYNC] فشل تهيئة قاعدة البيانات:', e);
            return false;
        }
    }
    
    const p = await getPending();
    console.log('[SYNC] عدد العناصر للمزامنة:', p.length);
    
    if(p.length === 0){
        console.log('[SYNC] لا توجد بيانات معلقة للرفع');
        return true;
    }
    
    let successCount = 0;
    for(const item of p){
        try{
            console.log('[SYNC] جاري رفع العنصر ID:', item.id);
            
            const fd = new FormData();
            // إضافة جميع الحقول ما عدا id
            for(const key in item){
                if(key !== 'id' && item[key] !== null && item[key] !== undefined){
                    fd.append(key, String(item[key]));
                }
            }
            
            console.log('[SYNC] FormData contents:');
            for(let [key, value] of fd.entries()){
                console.log('  ', key, ':', value);
            }
            
            const r = await fetch(`/inspect/submit`, {
                method: 'POST',
                body: fd,
                credentials: 'include', // مهم لإرسال الكوكيز
                redirect: 'manual'
            });
            
            console.log('[SYNC] Response status:', r.status, r.ok);
            
            if(r.ok || r.status === 302){
                await del(item.id);
                successCount++;
                const remaining = p.length - successCount;
                upd('✅ رفعت الإجابات المخزنة (' + remaining + ' متبقية)');
                console.log('[SYNC] تم رفع العنصر بنجاح - المتبقي:', remaining);
            } else {
                console.log('[SYNC] فشل الرفع - status:', r.status);
                try {
                    const text = await r.text();
                    console.log('[SYNC] Response body:', text);
                } catch(e) {}
            }
        } catch(e){
            console.error('[SYNC] Sync error for item', item.id, ':', e);
            // لا نخرج من الحلقة، نحاول العنصر التالي
        }
    }
    
    console.log('[SYNC] انتهت المزامنة - نجح:', successCount, 'من', p.length);
    return successCount > 0;
}

// تحديث حالة المزامنة في الواجهة
function upd(m){
    const e=document.getElementById('sync-status');
    if(e){
        e.textContent=m;
        e.classList.remove('hidden');
        // إخفاء الرسالة بعد 5 ثواني
        setTimeout(()=>e.classList.add('hidden'), 5000);
    }
}

// دالة التعامل مع الإرسال عند عدم الاتصال
window.handleOfflineSubmit = async function(form, code){
    console.log('[OFFLINE] handleOfflineSubmit called - offline mode');
    
    const btn=form.querySelector('button[type=submit]');
    const originalText = btn.textContent;
    btn.disabled=true;
    btn.textContent='جاري الحفظ...';
    
    const formData = new FormData(form);
    const data = {};
    
    // جمع جميع البيانات من النموذج
    for(const [key, value] of formData.entries()){
        if(key !== 'code'){ // إزالة حقل code
            data[key] = value;
        }
    }
    
    console.log('[OFFLINE] Data to save:', data);
    
    try{
        await initDB(); // التأكد من تهيئة قاعدة البيانات
        await save(data);
        console.log('[OFFLINE] تم الحفظ في IndexedDB بنجاح', data);
        upd('💾 تم الحفظ محلياً - سيرفع عند الاتصال');
        
        // عرض رسالة واضحة للمستخدم
        alert('✅ تم حفظ البيانات محلياً بنجاح!\n\nسيتم رفعها تلقائياً عند الاتصال بالإنترنت.\nيمكنك إغلاق المتصفح والعودة لاحقاً.');
        
        setTimeout(()=>{
            window.location.href='/inspect/success';
        }, 1500);
    }catch(e){
        console.error('[OFFLINE] فشل الحفظ في IndexedDB:', e);
        // Fallback إلى localStorage
        localStorage.setItem('pending_submission_' + Date.now(), JSON.stringify(data));
        upd('💾 تم الحفظ محلياً (localStorage)');
        alert('✅ تم حفظ البيانات محلياً بنجاح!');
        setTimeout(()=>{
            window.location.href='/inspect/success';
        }, 1000);
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
        upd(navigator.onLine?'🌐 متصل':'📴 أوفلاين - يحفظ محلياً');
        
        window.addEventListener('online',()=>{
            console.log('[EVENT] الاتصال استُعيد - بدء المزامنة');
            upd('🔄 جاري المزامنة...');
            setTimeout(() => sync(), 500); // تأخير بسيط للتأكد من استقرار الاتصال
        });
        
        window.addEventListener('offline',()=>{
            console.log('[EVENT] انقطع الاتصال');
            upd('📴 انقطع - يحفظ محلياً')
        });
        
        // محاولة المزامنة كل 30 ثانية
        setInterval(sync,30000);
        
        // محاولة مزامنة فورية بعد ثانيتين
        setTimeout(sync, 2000);
    }catch(e){
        console.error('[INIT] فشل تهيئة IndexedDB:', e);
    }
});
