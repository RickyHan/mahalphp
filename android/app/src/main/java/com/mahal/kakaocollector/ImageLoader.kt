package com.mahal.kakaocollector

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Handler
import android.os.Looper
import android.util.LruCache
import android.widget.ImageView
import java.util.concurrent.Executors

/** 저장된 사진을 크기에 맞게 줄여 읽는 간단한 로더 (백그라운드 디코딩 + 메모리 캐시). */
object ImageLoader {
    private val executor = Executors.newFixedThreadPool(2)
    private val main = Handler(Looper.getMainLooper())
    private val cache = object : LruCache<String, Bitmap>((Runtime.getRuntime().maxMemory() / 8 / 1024).toInt()) {
        override fun sizeOf(key: String, value: Bitmap) = value.byteCount / 1024
    }

    /** 원본 해상도 (가로, 세로). 읽을 수 없으면 null */
    fun dimensions(path: String): Pair<Int, Int>? {
        val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(path, opts)
        return if (opts.outWidth > 0 && opts.outHeight > 0) opts.outWidth to opts.outHeight else null
    }

    fun decode(path: String, maxSide: Int): Bitmap? {
        val (w, h) = dimensions(path) ?: return null
        var sample = 1
        while (maxOf(w, h) / (sample * 2) >= maxSide) sample *= 2
        return BitmapFactory.decodeFile(path, BitmapFactory.Options().apply { inSampleSize = sample })
    }

    fun load(view: ImageView, path: String, maxSide: Int) {
        val key = "$path@$maxSide"
        view.tag = key
        cache.get(key)?.let { view.setImageBitmap(it); return }
        view.setImageDrawable(null)
        executor.execute {
            val bitmap = runCatching { decode(path, maxSide) }.getOrNull() ?: return@execute
            cache.put(key, bitmap)
            main.post { if (view.tag == key) view.setImageBitmap(bitmap) }
        }
    }
}
