package com.mahal.kakaocollector

import android.os.Bundle
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/** 서버 주소 · API 키 입력과 연결 확인 */
class ServerActivity : AppCompatActivity() {
    private val scope = MainScope()
    private lateinit var settings: AppSettings

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_server)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        settings = AppSettings(this)
        val url = findViewById<TextInputEditText>(R.id.serverUrl).apply { setText(settings.serverUrl) }
        val key = findViewById<TextInputEditText>(R.id.apiKey).apply { setText(settings.apiKey) }
        findViewById<MaterialButton>(R.id.save).setOnClickListener {
            settings.serverUrl = url.text.toString()
            settings.apiKey = key.text.toString()
            ServerCheck.reset()
            if (settings.isConfigured) UploadWorker.enqueue(this)
            Toast.makeText(this, "저장했습니다.", Toast.LENGTH_SHORT).show()
            test()
        }
        findViewById<MaterialButton>(R.id.testConnection).setOnClickListener { test() }
        findViewById<TextView>(R.id.connectionStatus).text = ServerCheck.lastMessage
    }

    private fun test() {
        val status = findViewById<TextView>(R.id.connectionStatus)
        status.text = "연결 확인 중…"
        scope.launch {
            ServerCheck.run(this@ServerActivity)
            status.text = ServerCheck.lastMessage
            StatusNotifier.update(this@ServerActivity)
        }
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }
}
