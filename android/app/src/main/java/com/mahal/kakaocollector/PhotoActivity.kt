package com.mahal.kakaocollector

import android.app.Activity
import android.os.Bundle
import android.widget.ImageView
import android.widget.TextView
import java.io.File

/** 저장된 사진 크게 보기. 하단에 보낸 사람·시각·해상도·용량 표시. 화면을 누르면 닫힌다. */
class PhotoActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_photo)
        val path = intent.getStringExtra(EXTRA_PATH) ?: return finish()
        val caption = intent.getStringExtra(EXTRA_CAPTION).orEmpty()
        title = getString(R.string.photo_desc)

        val metrics = resources.displayMetrics
        val image = findViewById<ImageView>(R.id.fullPhoto)
        ImageLoader.load(image, path, maxOf(metrics.widthPixels, metrics.heightPixels))
        image.setOnClickListener { finish() }

        val dims = ImageLoader.dimensions(path)?.let { (w, h) -> " · ${w}×${h}px" }.orEmpty()
        val kb = File(path).length() / 1024
        findViewById<TextView>(R.id.caption).text = "$caption$dims · ${kb}KB"
    }

    companion object {
        const val EXTRA_PATH = "path"
        const val EXTRA_CAPTION = "caption"
    }
}
