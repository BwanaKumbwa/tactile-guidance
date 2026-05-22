package com.example.hans

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import android.content.Intent
import android.os.Handler
import android.os.Looper

class HomescreenActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.homescreen)

        // Delay for 3 seconds (3000 ms), then go to Bluetooth activity
        Handler(Looper.getMainLooper()).postDelayed({
            val intent = Intent(this, BluetoothActivity::class.java)
            startActivity(intent)
            finish() // prevents returning to splash screen
        }, 3000)
    }
}

