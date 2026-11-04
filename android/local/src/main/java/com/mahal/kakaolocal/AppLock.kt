package com.mahal.kakaolocal

import android.app.Activity
import android.app.Application
import android.content.Intent
import android.os.Bundle
import android.view.WindowManager
import java.security.MessageDigest

/**
 * 앱 잠금. 비밀번호를 맞혀야 화면을 볼 수 있고, 앱이 화면에서 완전히 사라지면(홈·다른 앱·화면 끔) 다시 잠긴다.
 * 모든 화면은 캡처·최근 앱 미리보기가 막혀 있다(FLAG_SECURE).
 */
object AppLock {
    // 비밀번호는 코드에 그대로 두지 않고 SHA-256 으로만 비교한다
    private const val PIN_SHA256 = "36fc5ca5320e880b0d0f2166295b87f22db6b102bfa48a38b28ad0c0ba031c83"
    const val PIN_LENGTH = 6

    @Volatile var unlocked = false
        private set
    private var started = 0

    fun check(pin: String): Boolean = (sha256(pin) == PIN_SHA256).also { if (it) unlocked = true }

    fun lock() {
        unlocked = false
    }

    internal fun sha256(text: String): String =
        MessageDigest.getInstance("SHA-256").digest(text.toByteArray()).joinToString("") { "%02x".format(it) }

    /** Application 에서 한 번 등록: 모든 화면에 캡처 방지, 잠겨 있으면 잠금 화면으로 */
    fun install(app: Application) {
        app.registerActivityLifecycleCallbacks(object : Application.ActivityLifecycleCallbacks {
            override fun onActivityCreated(a: Activity, b: Bundle?) {
                a.window.setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE)
            }

            override fun onActivityStarted(a: Activity) {
                started++
            }

            override fun onActivityResumed(a: Activity) {
                if (!unlocked && a !is LockActivity) {
                    a.startActivity(Intent(a, LockActivity::class.java).putExtra(LockActivity.EXTRA_RETURN, true))
                    a.overridePendingTransition(0, 0)
                }
            }

            override fun onActivityStopped(a: Activity) {
                started--
                if (started <= 0 && !a.isChangingConfigurations) {
                    started = 0
                    lock()
                }
            }

            override fun onActivityPaused(a: Activity) = Unit
            override fun onActivitySaveInstanceState(a: Activity, b: Bundle) = Unit
            override fun onActivityDestroyed(a: Activity) = Unit
        })
    }
}

class LocalApp : Application() {
    override fun onCreate() {
        super.onCreate()
        AppLock.install(this)
    }
}
