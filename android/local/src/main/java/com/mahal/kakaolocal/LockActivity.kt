package com.mahal.kakaolocal

import android.content.Intent
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.widget.GridLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.android.material.button.MaterialButton

/** 비밀번호(숫자 6자리) 입력 화면. 앱을 열 때마다, 그리고 다른 앱에 다녀오면 다시 나온다. */
class LockActivity : AppCompatActivity() {

    private val pin = StringBuilder()
    private lateinit var dots: List<View>
    private lateinit var message: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (AppLock.unlocked && !intent.getBooleanExtra(EXTRA_RETURN, false)) {
            openHome()
            return
        }
        setContentView(buildView())
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                moveTaskToBack(true) // 잠금 화면에서 뒤로 가도 내용이 보이지 않게
            }
        })
    }

    private fun buildView(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setBackgroundColor(ContextCompat.getColor(context, R.color.surface))
            setPadding(dp(24), dp(72), dp(24), dp(24))
        }
        root.addView(TextView(this).apply {
            text = "🔒"
            textSize = 40f
            gravity = Gravity.CENTER
        })
        root.addView(TextView(this).apply {
            text = getString(R.string.app_name)
            textSize = 22f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(ContextCompat.getColor(context, R.color.text_primary))
            gravity = Gravity.CENTER
            setPadding(0, dp(12), 0, dp(4))
        })
        message = TextView(this).apply {
            text = "비밀번호 6자리를 입력하십시오"
            textSize = 14f
            setTextColor(ContextCompat.getColor(context, R.color.text_secondary))
            gravity = Gravity.CENTER
        }
        root.addView(message)

        val dotRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            setPadding(0, dp(28), 0, dp(36))
        }
        dots = List(AppLock.PIN_LENGTH) {
            View(this).also { v ->
                dotRow.addView(v, LinearLayout.LayoutParams(dp(16), dp(16)).apply { setMargins(dp(9), 0, dp(9), 0) })
            }
        }
        root.addView(dotRow)
        renderDots()

        val pad = GridLayout(this).apply { columnCount = 3 }
        val keys = listOf("1", "2", "3", "4", "5", "6", "7", "8", "9", "지움", "0", "확인")
        for (k in keys) {
            val b = MaterialButton(this, null, com.google.android.material.R.attr.materialButtonOutlinedStyle).apply {
                text = k
                textSize = if (k.length == 1) 24f else 16f
                setTextColor(ContextCompat.getColor(context, R.color.text_primary))
                cornerRadius = dp(36)
                insetTop = 0
                insetBottom = 0
                setOnClickListener { onKey(k) }
            }
            pad.addView(b, GridLayout.LayoutParams().apply {
                width = dp(84)
                height = dp(72)
                setMargins(dp(8), dp(8), dp(8), dp(8))
            })
        }
        root.addView(pad)
        return root
    }

    private fun onKey(k: String) {
        when (k) {
            "지움" -> if (pin.isNotEmpty()) pin.deleteCharAt(pin.length - 1)
            "확인" -> submit()
            else -> if (pin.length < AppLock.PIN_LENGTH) pin.append(k)
        }
        renderDots()
        if (pin.length == AppLock.PIN_LENGTH && k != "확인") submit()
    }

    private fun submit() {
        if (AppLock.check(pin.toString())) {
            if (intent.getBooleanExtra(EXTRA_RETURN, false)) finish() else openHome()
            return
        }
        pin.clear()
        renderDots()
        message.text = "비밀번호가 맞지 않습니다. 다시 입력하십시오"
        message.setTextColor(ContextCompat.getColor(this, R.color.bad))
    }

    private fun renderDots() {
        dots.forEachIndexed { i, v ->
            v.background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                if (i < pin.length) setColor(ContextCompat.getColor(this@LockActivity, R.color.brand))
                else setStroke(dp(2), ContextCompat.getColor(this@LockActivity, R.color.outline))
            }
        }
    }

    private fun openHome() {
        startActivity(Intent(this, MainActivity::class.java))
        finish()
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    companion object {
        /** true 면 잠금을 푼 뒤 원래 보던 화면으로 돌아간다 */
        const val EXTRA_RETURN = "return"
    }
}
