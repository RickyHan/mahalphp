(() => {
  const messages = {
    invalid: "아이디 또는 비밀번호가 올바르지 않습니다.",
    locked: "로그인 실패가 반복되어 10분간 차단되었습니다. 잠시 후 다시 시도하십시오.",
  };
  const code = new URLSearchParams(location.search).get("error");
  const box = document.getElementById("login-error");
  if (code && messages[code]) {
    box.textContent = messages[code];
    box.hidden = false;
  }
})();
