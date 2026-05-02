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
        const t=db.transaction(STORE,'readwrite');
        t.objectStore(STORE).add(d);
        t.oncomplete=()=>{res()};
        t.onerror=rej
    })
}

// جلب جميع البيانات المعلقة
function getPending(){
    return new Promise((res,rej)=>{
        const t=db.transaction(STORE,'readonly');
        const r=t.objectStore(STORE).getAll();
        r.onsuccess=()=>res(r.result||[]);
        r.onerror=rej
    })
}

// حذف عنصر بعد الرفع الناجح
function del(id){
    return new Promise((res,rej)=>{
        const t=db.transaction(STORE,'readwrite');
        t.objectStore(STORE).delete(id);
        t.oncomplete=res;
        t.onerror=rej
    })
}

// مزامنة البيانات مع السيرفر
async function sync(){
    if(!navigator.onLine){
        console.log('لا يوجد اتصال - تأجيل المزامنة');
        return;
    }
    const p=await getPending();
    console.log('عدد العناصر للمزامنة:', p.length);
    for(const i of p){
        try{
            const fd=new FormData();
            Object.keys(i).forEach(k=>{
                if(k !== 'id') fd.append(k,i[k])
            });
            const r=await fetch(`/inspect/submit`,{
                method:'POST',
                body:fd,
                redirect:'manual'
            });
            if(r.ok||r.status===302){
                await del(i.id);
                upd('✅ رفعت الإجابات المخزنة (' + (p.length-1) + ' متبقية)');
                console.log('تم رفع العنصر بنجاح');
            }else{
                console.log('فشل الرفع - status:', r.status);
            }
        }catch(e){
            console.error('Sync error:',e);
            break
        }
    }
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
    console.log('handleOfflineSubmit called - offline mode');
    const btn=form.querySelector('button[type=submit]');
    btn.disabled=true;
    btn.textContent='جاري الحفظ...';
    
    const formData = new FormData(form);
    const data = {
        code: code,
        session_id: formData.get('session_id'),
        ...Object.fromEntries(formData)
    };
    
    // إزالة الحقول غير الضرورية من البيانات المحفوظة
    delete data.code;
    
    try{
        await save(data);
        console.log('تم الحفظ في IndexedDB بنجاح');
        upd('💾 تم الحفظ محلياً - سيرفع عند الاتصال');
        setTimeout(()=>{
            window.location.href='/inspect/success';
        }, 1000);
    }catch(e){
        console.error('فشل الحفظ في IndexedDB:', e);
        // Fallback إلى localStorage
        localStorage.setItem('pending_submission_' + Date.now(), JSON.stringify(data));
        upd('💾 تم الحفظ محلياً (localStorage)');
        setTimeout(()=>{
            window.location.href='/inspect/success';
        }, 1000);
    }
}

// التهيئة عند تحميل الصفحة
document.addEventListener('DOMContentLoaded', async ()=>{
    try{
        await initDB();
        console.log('IndexedDB initialized');
        upd(navigator.onLine?'🌐 متصل':'📴 أوفلاين - يحفظ محلياً');
        
        window.addEventListener('online',()=>{
            console.log('الاتصال استُعيد - بدء المزامنة');
            upd('🔄 مزامنة...');
            sync()
        });
        
        window.addEventListener('offline',()=>{
            console.log('انقطع الاتصال');
            upd('📴 انقطع - يحفظ محلياً')
        });
        
        // محاولة المزامنة كل 60 ثانية
        setInterval(sync,60000);
        
        // محاولة مزامنة فورية
        setTimeout(sync, 2000);
    }catch(e){
        console.error('فشل تهيئة IndexedDB:', e);
    }
});