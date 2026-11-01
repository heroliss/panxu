"""The same disposable protocol peer, on the current source version."""
import serve_v050_fixture as fixture
from serve_v060_fixture import answer

if __name__ == '__main__':
    fixture.answer = answer
    fixture.main()
